/** Typed client for the control-plane API. Session lives in an HttpOnly cookie; the CSRF token
 *  is kept in sessionStorage because the server only reveals it at login. */

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const CSRF_KEY = "iptv.csrf";

export const getCsrf = (): string => sessionStorage.getItem(CSRF_KEY) ?? "";

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (method !== "GET" && method !== "HEAD") headers.set("X-CSRF-Token", getCsrf());
  if (init.body) headers.set("Content-Type", "application/json");

  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = (await response.json()) as { detail?: unknown };
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export interface Me {
  email: string;
  last_login_at: string | null;
}

export interface SourceOut {
  id: number;
  name: string;
  type: string;
  enabled: boolean;
  stream_count: number;
  last_status: string | null;
  last_import_at: string | null;
  url_masked: string;
}

export interface ImportStats {
  parsed: number;
  created: number;
  updated: number;
  unchanged: number;
  stale: number;
  channels_created: number;
  channels_linked: number;
}

export interface ChannelOut {
  id: string;
  public_key: string;
  name: string;
  number: number | null;
  group: string | null;
  tvg_id: string | null;
  sources: number;
}

export interface ChannelPage {
  total: number;
  items: ChannelOut[];
}

export interface DeviceOut {
  id: string;
  name: string;
  kind: string;
  token_prefix: string;
  m3u_path: string;
  created_at: string;
}

export interface DeviceWithToken extends DeviceOut {
  token: string;
}

export interface ProfileOut {
  id: number;
  name: string;
  strategy: string;
  is_default: boolean;
  devices: number;
  channels_visible: number;
  channels_total: number;
}

export interface SchedulerStatus {
  healthy: boolean;
  running: boolean;
  owner: string | null;
  last_tick_epoch: number | null;
  seconds_since_tick: number | null;
  stale_after_seconds: number;
  rounds: number;
  probed_total: number;
  last_error: string | null;
}

export interface EpgStatus {
  sources: number;
  epg_channels: number;
  programmes: number;
  mapped_channels: number;
  unmapped_channels: number;
}

export interface StreamHealth {
  stream_id: number;
  source: string;
  name: string;
  container: string;
  score: number;
  uptime_ratio: number;
  checks: number;
  failures: number;
  p90_ttfb_ms: number | null;
  resolution: string | null;
  video_codec: string | null;
  last_error: string | null;
}

export async function login(email: string, password: string, totp: string): Promise<void> {
  const data = await request<{ email: string; csrf_token: string; expires_in: number }>(
    "/api/v1/auth/login",
    { method: "POST", body: JSON.stringify({ email, password, totp }) },
  );
  sessionStorage.setItem(CSRF_KEY, data.csrf_token);
}

export async function logout(): Promise<void> {
  try {
    await request<void>("/api/v1/auth/logout", { method: "POST" });
  } finally {
    sessionStorage.removeItem(CSRF_KEY);
  }
}

export const me = (): Promise<Me> => request<Me>("/api/v1/auth/me");

export const listSources = (): Promise<SourceOut[]> => request<SourceOut[]>("/api/v1/sources");

export const createSource = (name: string, url: string): Promise<SourceOut> =>
  request<SourceOut>("/api/v1/sources", {
    method: "POST",
    body: JSON.stringify({ name, url, type: "m3u" }),
  });

export const importM3u = (sourceId: number, m3u: string): Promise<{ stats: ImportStats }> =>
  request<{ stats: ImportStats }>(`/api/v1/sources/${sourceId}/import`, {
    method: "POST",
    body: JSON.stringify({ m3u }),
  });

export const listChannels = (search: string, limit: number, offset: number): Promise<ChannelPage> => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (search) params.set("search", search);
  return request<ChannelPage>(`/api/v1/channels?${params.toString()}`);
};

export const listDevices = (): Promise<DeviceOut[]> => request<DeviceOut[]>("/api/v1/devices");

export const createDevice = (name: string, kind: string, profileId?: number): Promise<DeviceWithToken> =>
  request<DeviceWithToken>("/api/v1/devices", {
    method: "POST",
    body: JSON.stringify({ name, kind, profile_id: profileId ?? null }),
  });

export const rotateDevice = (id: string): Promise<DeviceWithToken> =>
  request<DeviceWithToken>(`/api/v1/devices/${id}/rotate`, { method: "POST" });

export const listProfiles = (): Promise<ProfileOut[]> => request<ProfileOut[]>("/api/v1/profiles");

export const schedulerStatus = (): Promise<SchedulerStatus> =>
  request<SchedulerStatus>("/api/v1/scheduler/status");

export const runSchedulerTick = (): Promise<{ probed: number; ok: number; failed: number; skipped: boolean }> =>
  request("/api/v1/scheduler/tick", { method: "POST" });

export const epgStatus = (): Promise<EpgStatus> => request<EpgStatus>("/api/v1/epg/status");

export const healthStreams = (limit = 50): Promise<StreamHealth[]> =>
  request<StreamHealth[]>(`/api/v1/health/streams?limit=${limit}`);
