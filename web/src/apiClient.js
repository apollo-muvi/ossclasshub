const API_BASE = import.meta.env.VITE_API_BASE || "";
let cachedVersion = null;

export async function getApiVersion() {
  if (cachedVersion) return cachedVersion;
  const response = await fetch(`${API_BASE}/api/app-settings`);
  if (!response.ok) return "v1";
  const data = await response.json();
  cachedVersion = data.api_version || "v1";
  return cachedVersion;
}

export async function buildApiUrl(path) {
  const version = await getApiVersion();
  return `${API_BASE}/api/${version}${path}`;
}

function formatErrorDetail(detail, fallback) {
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        const location = Array.isArray(item?.loc) ? item.loc.join(".") : "";
        return [location, item?.msg].filter(Boolean).join(": ") || JSON.stringify(item);
      })
      .join("；");
  }
  return JSON.stringify(detail);
}

export async function request(path, options = {}) {
  const url = await buildApiUrl(path);
  const method = options.method || "GET";
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    const message = formatErrorDetail(detail.detail, `HTTP ${response.status}`);
    throw new Error(`${method} ${path}: ${message}`);
  }
  if (response.status === 204) return undefined;
  return response.json();
}

export async function uploadFile(path, file, options = {}) {
  const url = await buildApiUrl(path);
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(url, {
    method: "POST",
    body,
    headers: {
      ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    const fallback = response.status === 413 ? "圖片檔案太大，請縮小後再上傳" : `HTTP ${response.status}`;
    const message = response.status === 413 ? fallback : formatErrorDetail(detail.detail, fallback);
    throw new Error(message);
  }
  return response.json();
}

export async function fetchAuthorizedBlobUrl(path, token) {
  const url = await buildApiUrl(path);
  const response = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

export async function downloadAuthorizedBlob(path, token) {
  const url = await buildApiUrl(path);
  const response = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(formatErrorDetail(detail.detail, `HTTP ${response.status}`));
  }
  return response.blob();
}

export async function getAppSettings() {
  const url = await buildApiUrl("/app-settings");
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}
