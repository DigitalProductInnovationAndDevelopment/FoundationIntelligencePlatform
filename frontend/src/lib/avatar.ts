export function avatarInitials(identity: unknown, authenticated: boolean): string {
  if (!authenticated) return "NG";
  const raw = typeof identity === "string" ? identity.trim() : "";
  const localPart = raw.split("@", 1)[0] || "";
  const components = localPart
    .split(/[._-]+/)
    .map(component => component.replace(/[^a-z0-9]/gi, ""))
    .filter(Boolean);
  if (components.length >= 2) {
    return `${components[0][0]}${components[1][0]}`.toUpperCase();
  }
  const usable = (components[0] || localPart).replace(/[^a-z0-9]/gi, "");
  return usable.slice(0, 2).toUpperCase() || "AU";
}
