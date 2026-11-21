/**
 * Build-time configuration read from the environment.
 *
 * `NEXT_PUBLIC_API_URL` must point at the backend **as reachable from the
 * browser** (not from inside a container), e.g. `http://localhost:8000`.
 */
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Minimum password length — must match `settings.password_min_length` on the backend. */
export const PASSWORD_MIN_LENGTH = 12;

/** localStorage key holding the selected workspace id. */
export const ACTIVE_WORKSPACE_KEY = "ocp.activeWorkspaceId";

/** localStorage / cookie key for the colour theme. */
export const THEME_KEY = "ocp.theme";
