from pathlib import Path
import json
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra" / "cloudformation" / "demo.yaml"
DB_PREREQUISITE = ROOT / "infra" / "cloudformation" / "db-access-prerequisite.yaml"
PARAMETERS = ROOT / "infra" / "cloudformation" / "parameters.demo.example.json"
NGINX = ROOT / "docker" / "frontend-nginx.ecs.conf"
MANAGED_CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
MANAGED_ALL_VIEWER_EXCEPT_HOST = "b689b0a8-53d0-40ab-baf2-68738e2966ac"


def _block(source: str, name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>(?:(?!^  \S).*(?:\n|$))*)",
        source,
        flags=re.MULTILINE,
    )
    if match is None:
        raise AssertionError(f"CloudFormation block {name} was not found")
    return match.group(0)


class TestCloudFormationCustomerAccessContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = TEMPLATE.read_text(encoding="utf-8")
        cls.db_prerequisite = DB_PREREQUISITE.read_text(encoding="utf-8")
        cls.parameters = json.loads(PARAMETERS.read_text(encoding="utf-8"))
        cls.nginx = NGINX.read_text(encoding="utf-8")

    def test_deployment_states_and_prefix_list_assertion(self):
        lockdown = _block(self.template, "OriginLockdownEnabled")
        prefix = _block(self.template, "CloudFrontOriginPrefixListId")
        rule = _block(self.template, "RequireCloudFrontPrefixListForLockdown")
        self.assertIn("Default: 'false'", lockdown)
        self.assertIn("AllowedValues: ['false', 'true']", lockdown)
        self.assertIn("Default: ''", prefix)
        self.assertIn("RuleCondition: !Equals [!Ref OriginLockdownEnabled, 'true']", rule)
        self.assertIn("!Ref CloudFrontOriginPrefixListId", rule)
        self.assertNotRegex(prefix, r"pl-[a-f0-9]{8,}")

    def test_state_a_and_b_use_mutually_exclusive_ingress(self):
        public = _block(self.template, "PublicAlbHttpIngress")
        cloudfront = _block(self.template, "CloudFrontAlbHttpIngress")
        self.assertIn("Condition: PublicOriginAccess", public)
        self.assertIn("CidrIp: 0.0.0.0/0", public)
        self.assertIn("FromPort: 80", public)
        self.assertIn("Condition: OriginLockdown", cloudfront)
        self.assertIn("SourcePrefixListId: !Ref CloudFrontOriginPrefixListId", cloudfront)
        self.assertIn("FromPort: 80", cloudfront)
        self.assertNotIn("CidrIp: 0.0.0.0/0", cloudfront)

    def test_listener_forwards_in_a_and_defaults_to_403_in_b(self):
        listener = _block(self.template, "HttpListener")
        verification = _block(self.template, "OriginVerificationListenerRule")
        self.assertIn("!If", listener)
        self.assertIn("OriginLockdown", listener)
        self.assertIn("Type: fixed-response", listener)
        self.assertIn("StatusCode: '403'", listener)
        self.assertIn("Type: forward", listener)
        self.assertIn("HttpHeaderName: X-FIP-Origin-Verify", verification)
        self.assertIn("Values: [!Ref OriginVerificationToken]", verification)
        self.assertIn("TargetGroupArn: !Ref FrontendTargetGroup", verification)

    def test_cloudfront_default_and_api_behaviors(self):
        distribution = _block(self.template, "CloudFrontDistribution")
        self.assertIn("CloudFrontDefaultCertificate: true", distribution)
        self.assertIn("ViewerProtocolPolicy: redirect-to-https", distribution)
        self.assertIn("DefaultRootObject: index.html", distribution)
        self.assertIn("AllowedMethods: [GET, HEAD, OPTIONS]", distribution)
        self.assertIn("PathPattern: /api/*", distribution)
        self.assertIn(
            "AllowedMethods: [GET, HEAD, OPTIONS, PUT, PATCH, POST, DELETE]",
            distribution,
        )
        self.assertIn("OriginProtocolPolicy: http-only", distribution)
        self.assertIn("HeaderName: X-FIP-Origin-Verify", distribution)
        self.assertIn("HeaderValue: !Ref OriginVerificationToken", distribution)

    def test_caching_authorization_query_and_host_contract(self):
        distribution = _block(self.template, "CloudFrontDistribution")
        default_origin = _block(self.template, "DefaultOriginRequestPolicy")
        self.assertEqual(distribution.count(f"CachePolicyId: {MANAGED_CACHING_DISABLED}"), 2)
        self.assertIn(
            f"OriginRequestPolicyId: {MANAGED_ALL_VIEWER_EXCEPT_HOST}",
            distribution,
        )
        self.assertIn("QueryStringBehavior: all", default_origin)
        self.assertIn("CookieBehavior: none", default_origin)
        self.assertIn("HeaderBehavior: none", default_origin)
        self.assertNotRegex(default_origin, r"(?im)^\s*- Host\s*$")

    def test_no_invalid_custom_disabled_cache_policy_regression(self):
        self.assertNotIn("DisabledDefaultCachePolicy:", self.template)
        self.assertNotIn("DisabledApiCachePolicy:", self.template)
        self.assertNotIn("Type: AWS::CloudFront::CachePolicy", self.template)
        self.assertNotRegex(
            self.template,
            r"(?s)DefaultTTL:\s*0.*ParametersInCacheKeyAndForwardedToOrigin",
        )

    def test_origin_verification_token_is_noecho_and_only_placeholder_is_committed(self):
        token = _block(self.template, "OriginVerificationToken")
        self.assertIn("NoEcho: true", token)
        self.assertIn("MinLength: 43", token)
        values = {item["ParameterKey"]: item["ParameterValue"] for item in self.parameters}
        self.assertEqual(
            values["OriginVerificationToken"],
            "REPLACE_WITH_43_PLUS_BASE64URL_RANDOM_VALUE",
        )

    def test_cognito_managed_login_mfa_and_public_pkce_client(self):
        pool = _block(self.template, "CognitoUserPool")
        client = _block(self.template, "CognitoUserPoolClient")
        domain = _block(self.template, "CognitoUserPoolDomain")
        branding = _block(self.template, "CognitoManagedLoginBranding")
        self.assertIn("AllowAdminCreateUserOnly: true", pool)
        self.assertIn("MfaConfiguration: 'ON'", pool)
        self.assertIn("EnabledMfas: [SOFTWARE_TOKEN_MFA]", pool)
        self.assertNotIn("SMS_MFA", pool)
        self.assertNotIn("SoftwareTokenMfaConfiguration", pool)
        self.assertIn("MinimumLength: 12", pool)
        self.assertIn("GenerateSecret: false", client)
        self.assertIn("AllowedOAuthFlows: [code]", client)
        self.assertNotIn("implicit", client.lower())
        self.assertIn("${CloudFrontDistribution.DomainName}/auth/callback", client)
        self.assertIn("${CloudFrontDistribution.DomainName}/'", client)
        self.assertIn("ManagedLoginVersion: 2", domain)
        self.assertIn("UseCognitoProvidedValues: true", branding)

    def test_cognito_deletion_protection_follows_deployment_state(self):
        pool = _block(self.template, "CognitoUserPool")
        self.assertIn(
            "DeletionProtection: !If [OriginLockdown, ACTIVE, INACTIVE]",
            pool,
        )
        self.assertEqual(
            "false",
            next(
                item["ParameterValue"]
                for item in self.parameters
                if item["ParameterKey"] == "OriginLockdownEnabled"
            ),
        )

    def test_three_groups_and_least_privilege_task_role(self):
        for logical_id, group in (
            ("CustomerGroup", "customer"),
            ("OperatorGroup", "operator"),
            ("AdminGroup", "admin"),
        ):
            self.assertIn(f"GroupName: {group}", _block(self.template, logical_id))
        task_role = _block(self.template, "ApplicationTaskRole")
        self.assertIn("cognito-idp:AdminGetUser", task_role)
        self.assertIn("cognito-idp:ListUsersInGroup", task_role)
        self.assertNotIn("cognito-idp:*", task_role)
        self.assertIn("Resource: !GetAtt CognitoUserPool.Arn", task_role)

    def test_backend_and_nginx_runtime_contract(self):
        task = _block(self.template, "ApplicationTaskDefinition")
        self.assertIn("Value: cognito_rbac", task)
        self.assertIn("Name: COGNITO_USER_POOL_ID", task)
        self.assertIn("Name: COGNITO_CLIENT_ID", task)
        self.assertIn("proxy_pass http://127.0.0.1:8000;", self.nginx)
        self.assertNotIn("ssl_certificate", self.nginx)

    def test_runtime_reader_writer_and_migration_secrets_are_separated(self):
        application = _block(self.template, "ApplicationTaskDefinition")
        application_execution = _block(self.template, "EcsExecutionRole")
        migration = _block(self.template, "MigrationTaskDefinition")
        migration_execution = _block(self.template, "MigrationExecutionRole")
        self.assertIn("Name: DATABASE_USER", application)
        self.assertIn("Name: DATABASE_WRITE_USER", application)
        self.assertIn("Name: DATABASE_WRITE_PASSWORD", application)
        self.assertNotIn("DATABASE_ADMIN", application)
        self.assertNotIn("MasterUserSecret", application_execution)
        self.assertIn("ExecutionRoleArn: !GetAtt MigrationExecutionRole.Arn", migration)
        self.assertIn("MasterUserSecret", migration_execution)

    def test_worker_sidecar_is_private_and_uses_backend_runtime(self):
        application = _block(self.template, "ApplicationTaskDefinition")
        service = _block(self.template, "ApplicationService")
        worker = application.split("- Name: worker", 1)[1]
        self.assertIn("Image: !Ref BackendImageUri", worker)
        self.assertIn("EntryPoint: [python, -m]", worker)
        self.assertIn("Command: [pipelines.worker]", worker)
        self.assertIn("Name: DATABASE_PIPELINE_PASSWORD", worker)
        self.assertIn("Name: PIPELINE_SNAPSHOT_S3_URI", worker)
        self.assertNotIn("PortMappings:", worker)
        self.assertNotIn("ContainerName: worker", service)
        self.assertIn("ContainerName: frontend", service)
        self.assertIn("Cpu: !If [PipelineWorkerEnabled, '1024', '512']", application)
        self.assertIn("Memory: !If [PipelineWorkerEnabled, '4096', '2048']", application)

    def test_pipeline_publisher_secret_and_snapshot_permissions_are_scoped(self):
        execution = _block(self.template, "EcsExecutionRole")
        task_role = _block(self.template, "ApplicationTaskRole")
        prerequisite = _block(self.db_prerequisite, "DbAccessTaskDefinition")
        self.assertIn("ApplicationDatabasePipelineSecretArn", execution)
        self.assertIn("DiscoverCurrentSnapshotFallback", task_role)
        self.assertIn("Action: s3:ListBucket", task_role)
        self.assertIn("s3:prefix: pipeline-snapshots/current.db", task_role)
        self.assertIn("s3:GetObject", task_role)
        self.assertIn("s3:PutObject", task_role)
        self.assertIn("pipeline-snapshots/current.db", task_role)
        self.assertNotIn("s3:*", task_role)
        self.assertIn("DATABASE_PIPELINE_PASSWORD", prerequisite)

    def test_external_api_secrets_are_injected_only_where_needed(self):
        parameters = self.template.split("\nRules:\n", 1)[0]
        application = _block(self.template, "ApplicationTaskDefinition")
        execution = _block(self.template, "EcsExecutionRole")
        backend, worker = application.split("- Name: worker", 1)
        self.assertIn("AnthropicSecretArn", parameters)
        self.assertIn("CharityCommissionSecretArn", parameters)
        self.assertIn("Name: ANTHROPIC_API_KEY", backend)
        self.assertIn("${AnthropicSecretArn}:api_key::", backend)
        self.assertNotIn("CHARITY_COMMISSION_API_KEY", backend)
        self.assertIn("Name: CHARITY_COMMISSION_API_KEY", worker)
        self.assertIn("${CharityCommissionSecretArn}:api_key::", worker)
        self.assertNotIn("ANTHROPIC_API_KEY", worker)
        self.assertIn("!Ref AnthropicSecretArn", execution)
        self.assertIn("!Ref CharityCommissionSecretArn", execution)
        self.assertNotIn("Resource: '*'", execution)
        self.assertNotIn("kms:Decrypt", execution)

    def test_db_prerequisite_stack_has_no_application_cutover_or_edge_resources(self):
        source = self.db_prerequisite
        self.assertIn("Type: AWS::SecretsManager::Secret", source)
        self.assertIn("Type: AWS::ECS::TaskDefinition", source)
        self.assertIn("Command: [migration.database_access]", source)
        self.assertIn("CpuArchitecture: ARM64", source)
        self.assertIn("User: '10001:10001'", source)
        for forbidden in (
            "AWS::ECS::Service",
            "AWS::RDS::DBInstance",
            "AWS::CloudFront::Distribution",
            "AWS::Cognito::",
            "AWS::ElasticLoadBalancingV2::",
            "AWS::EC2::VPC",
        ):
            self.assertNotIn(forbidden, source)

    def test_db_prerequisite_task_has_release_secrets_but_no_secret_outputs(self):
        task = _block(self.db_prerequisite, "DbAccessTaskDefinition")
        outputs = self.db_prerequisite.split("\nOutputs:\n", 1)[1]
        self.assertIn("DATABASE_ADMIN_PASSWORD", task)
        self.assertIn("DATABASE_READER_PASSWORD", task)
        self.assertIn("DATABASE_WRITER_PASSWORD", task)
        self.assertIn("DATABASE_PIPELINE_PASSWORD", task)
        self.assertIn("WriterDatabaseSecretArn", outputs)
        self.assertIn("PipelineDatabaseSecretArn", outputs)
        self.assertNotIn("password", outputs.lower())

    def test_outputs_do_not_expose_origin_token(self):
        outputs = self.template.split("\nOutputs:\n", 1)[1]
        self.assertIn("https://${CloudFrontDistribution.DomainName}", outputs)
        self.assertNotIn("OriginVerificationToken", outputs)


if __name__ == "__main__":
    unittest.main()
