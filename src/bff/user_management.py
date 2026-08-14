"""Fail-closed Cognito user administration for the three application roles."""

from __future__ import annotations

from typing import Any, cast, Literal, Mapping, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from bff.config import SECURITY_SETTINGS
from bff.security import APP_ROLES, Principal, Role, require_roles


CognitoRole = Literal["customer", "operator", "admin"]
APP_GROUP_NAMES = frozenset(role.value for role in APP_ROLES)


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(min_length=1, max_length=32)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized.count("@") != 1 or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("A valid email address is required")
        return normalized


class UserRoleUpdate(BaseModel):
    role: str = Field(min_length=1, max_length=32)


class UserRecord(BaseModel):
    id: str
    email: Optional[str] = None
    enabled: bool
    status: str
    role: Optional[CognitoRole] = None


class UserPage(BaseModel):
    users: list[UserRecord]
    next_token: Optional[str] = None


router = APIRouter(prefix="/api/admin/users", tags=["User Management"])


def _settings(request: Request):
    settings = getattr(request.app.state, "security_settings", SECURITY_SETTINGS)
    if settings.auth_mode != "cognito_rbac" or not settings.cognito_user_pool_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return settings


def _client(request: Request):
    existing = getattr(request.app.state, "cognito_client", None)
    if existing is not None:
        return existing
    settings = _settings(request)
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - deployment dependency contract
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity administration is temporarily unavailable.",
        ) from exc
    client = boto3.client("cognito-idp", region_name=settings.cognito_region)
    request.app.state.cognito_client = client
    return client


def _pool_id(request: Request) -> str:
    return str(_settings(request).cognito_user_pool_id)


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", {})
    if isinstance(response, Mapping):
        error = response.get("Error", {})
        if isinstance(error, Mapping):
            return str(error.get("Code") or "")
    return ""


def _raise_safe_cognito_error(exc: Exception) -> None:
    code = _error_code(exc)
    if code in {"UsernameExistsException", "AliasExistsException"}:
        raise HTTPException(status_code=409, detail="A user with this email already exists.") from exc
    if code == "UserNotFoundException":
        raise HTTPException(status_code=404, detail="User not found.") from exc
    if code in {"InvalidParameterException", "InvalidPasswordException"}:
        raise HTTPException(status_code=400, detail="The identity provider rejected the request.") from exc
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Identity provider operation failed.",
    ) from exc


def _attribute_map(user: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in user.get("Attributes", []) or user.get("UserAttributes", []):
        if isinstance(item, Mapping) and isinstance(item.get("Name"), str):
            result[str(item["Name"])] = str(item.get("Value") or "")
    return result


def _validated_role(value: str) -> CognitoRole:
    normalized = value.strip().lower()
    if normalized not in APP_GROUP_NAMES:
        raise HTTPException(status_code=400, detail="Role must be customer, operator or admin.")
    return cast(CognitoRole, normalized)


def _groups_for_user(client: Any, pool_id: str, username: str) -> set[str]:
    groups: set[str] = set()
    next_token: Optional[str] = None
    while True:
        arguments: dict[str, Any] = {
            "UserPoolId": pool_id,
            "Username": username,
            "Limit": 60,
        }
        if next_token:
            arguments["NextToken"] = next_token
        response = client.admin_list_groups_for_user(**arguments)
        groups.update(
            str(group.get("GroupName"))
            for group in response.get("Groups", [])
            if isinstance(group, Mapping) and group.get("GroupName") in APP_GROUP_NAMES
        )
        next_token = response.get("NextToken")
        if not next_token:
            return groups


def _record(client: Any, pool_id: str, user: Mapping[str, Any]) -> UserRecord:
    username = str(user.get("Username") or "")
    groups = _groups_for_user(client, pool_id, username)
    role: Optional[CognitoRole] = None
    if len(groups) == 1:
        role = cast(CognitoRole, next(iter(groups)))
    attributes = _attribute_map(user)
    return UserRecord(
        id=username,
        email=attributes.get("email") or None,
        enabled=bool(user.get("Enabled", False)),
        status=str(user.get("UserStatus") or "UNKNOWN"),
        role=role,
    )


def _get_user(client: Any, pool_id: str, username: str) -> Mapping[str, Any]:
    try:
        return client.admin_get_user(UserPoolId=pool_id, Username=username)
    except Exception as exc:  # SDK exceptions are intentionally translated centrally.
        _raise_safe_cognito_error(exc)
        raise AssertionError("unreachable")


def _target_is_current_user(target: Mapping[str, Any], principal: Principal) -> bool:
    target_sub = _attribute_map(target).get("sub")
    return bool(target_sub and target_sub == principal.actor_id)


def _active_admin_usernames(client: Any, pool_id: str) -> list[str]:
    usernames: list[str] = []
    next_token: Optional[str] = None
    while True:
        arguments: dict[str, Any] = {
            "UserPoolId": pool_id,
            "GroupName": Role.ADMIN.value,
            "Limit": 60,
        }
        if next_token:
            arguments["NextToken"] = next_token
        response = client.list_users_in_group(**arguments)
        for listed in response.get("Users", []):
            username = str(listed.get("Username") or "")
            if not username:
                continue
            current = _get_user(client, pool_id, username)
            if bool(current.get("Enabled", False)):
                usernames.append(username)
        next_token = response.get("NextToken")
        if not next_token:
            return usernames


def _protect_admin_change(
    client: Any,
    pool_id: str,
    username: str,
    target: Mapping[str, Any],
    principal: Principal,
) -> None:
    if _target_is_current_user(target, principal):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Administrators cannot disable or downgrade their own account.",
        )
    active_admins = _active_admin_usernames(client, pool_id)
    if username in active_admins and len(active_admins) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The last active administrator cannot be disabled or downgraded.",
        )


@router.get("", response_model=UserPage)
async def list_users(
    request: Request,
    page_size: int = Query(default=25, ge=1, le=50),
    next_token: Optional[str] = Query(default=None, min_length=1, max_length=4096),
    _: Principal = Depends(require_roles(Role.ADMIN, action="users.list")),
):
    client = _client(request)
    pool_id = _pool_id(request)
    arguments: dict[str, Any] = {"UserPoolId": pool_id, "Limit": page_size}
    if next_token:
        arguments["PaginationToken"] = next_token
    try:
        response = client.list_users(**arguments)
        users = [_record(client, pool_id, user) for user in response.get("Users", [])]
    except HTTPException:
        raise
    except Exception as exc:
        _raise_safe_cognito_error(exc)
    return UserPage(users=users, next_token=response.get("PaginationToken"))


@router.post("", response_model=UserRecord, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    request: Request,
    _: Principal = Depends(
        require_roles(Role.ADMIN, action="users.create", idempotent=True)
    ),
):
    client = _client(request)
    pool_id = _pool_id(request)
    role = _validated_role(payload.role)
    try:
        response = client.admin_create_user(
            UserPoolId=pool_id,
            Username=payload.email,
            UserAttributes=[{"Name": "email", "Value": payload.email}],
            DesiredDeliveryMediums=["EMAIL"],
        )
        username = str(response["User"]["Username"])
        client.admin_add_user_to_group(
            UserPoolId=pool_id,
            Username=username,
            GroupName=role,
        )
        user = _get_user(client, pool_id, username)
        return _record(client, pool_id, user)
    except HTTPException:
        raise
    except Exception as exc:
        _raise_safe_cognito_error(exc)


@router.patch("/{user_id}/role", response_model=UserRecord)
async def update_user_role(
    payload: UserRoleUpdate,
    request: Request,
    user_id: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(
        require_roles(Role.ADMIN, action="users.role.update", idempotent=True)
    ),
):
    client = _client(request)
    pool_id = _pool_id(request)
    role = _validated_role(payload.role)
    target = _get_user(client, pool_id, user_id)
    groups = _groups_for_user(client, pool_id, user_id)
    if Role.ADMIN.value in groups and role != Role.ADMIN.value:
        _protect_admin_change(client, pool_id, user_id, target, principal)
    try:
        if role not in groups:
            client.admin_add_user_to_group(
                UserPoolId=pool_id,
                Username=user_id,
                GroupName=role,
            )
        for group in sorted(groups - {role}):
            client.admin_remove_user_from_group(
                UserPoolId=pool_id,
                Username=user_id,
                GroupName=group,
            )
        return _record(client, pool_id, _get_user(client, pool_id, user_id))
    except HTTPException:
        raise
    except Exception as exc:
        _raise_safe_cognito_error(exc)


@router.post("/{user_id}/disable", response_model=UserRecord)
async def disable_user(
    request: Request,
    user_id: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(
        require_roles(Role.ADMIN, action="users.disable", idempotent=True)
    ),
):
    client = _client(request)
    pool_id = _pool_id(request)
    target = _get_user(client, pool_id, user_id)
    groups = _groups_for_user(client, pool_id, user_id)
    if _target_is_current_user(target, principal):
        _protect_admin_change(client, pool_id, user_id, target, principal)
    if Role.ADMIN.value in groups:
        _protect_admin_change(client, pool_id, user_id, target, principal)
    try:
        client.admin_disable_user(UserPoolId=pool_id, Username=user_id)
        return _record(client, pool_id, _get_user(client, pool_id, user_id))
    except HTTPException:
        raise
    except Exception as exc:
        _raise_safe_cognito_error(exc)


@router.post("/{user_id}/enable", response_model=UserRecord)
async def enable_user(
    request: Request,
    user_id: str = Path(min_length=1, max_length=128),
    _: Principal = Depends(
        require_roles(Role.ADMIN, action="users.enable", idempotent=True)
    ),
):
    client = _client(request)
    pool_id = _pool_id(request)
    _get_user(client, pool_id, user_id)
    try:
        client.admin_enable_user(UserPoolId=pool_id, Username=user_id)
        return _record(client, pool_id, _get_user(client, pool_id, user_id))
    except HTTPException:
        raise
    except Exception as exc:
        _raise_safe_cognito_error(exc)


@router.post("/{user_id}/reset-password")
async def reset_user_password(
    request: Request,
    user_id: str = Path(min_length=1, max_length=128),
    _: Principal = Depends(
        require_roles(Role.ADMIN, action="users.password.reset", idempotent=True)
    ),
):
    client = _client(request)
    pool_id = _pool_id(request)
    _get_user(client, pool_id, user_id)
    try:
        client.admin_reset_user_password(UserPoolId=pool_id, Username=user_id)
    except Exception as exc:
        _raise_safe_cognito_error(exc)
    return {"message": "Password reset initiated."}


__all__ = ["router"]
