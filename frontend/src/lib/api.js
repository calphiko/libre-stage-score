export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

let csrfTokenCache = null;

function notifyAuthChanged() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("auth:changed"));
}

function getCookieValue(name) {
  if (typeof document === "undefined") return null;
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length !== 2) return null;
  return parts.pop().split(";").shift() ?? null;
}

function rememberCsrfToken(value) {
  if (!value || typeof value !== "string") return;
  csrfTokenCache = value.trim() || null;
}

function currentCsrfToken() {
  const fromCookie = getCookieValue("csrf_token");
  if (fromCookie) {
    rememberCsrfToken(fromCookie);
    return fromCookie;
  }
  return csrfTokenCache;
}

async function ensureCsrfToken() {
  const current = currentCsrfToken();
  if (current) return current;
  const res = await fetch(`${API_URL}/csrf`, { credentials: "include" });
  if (!res.ok) return null;
  const data = await res.json().catch(() => ({}));
  rememberCsrfToken(data.csrf_token);
  return currentCsrfToken();
}

async function fetchWithAuth(url, options = {}) {
  const requestOptions = {
    ...options,
    credentials: "include",
    headers: { ...(options.headers ?? {}) },
  };
  const method = (requestOptions.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const token = await ensureCsrfToken();
    if (token) requestOptions.headers["X-CSRF-Token"] = token;
  }

  let res = await fetch(url, requestOptions);
  if (res.status === 401) {
    const refresh = await fetch(`${API_URL}/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (refresh.ok) {
      const refreshed = await refresh.json().catch(() => ({}));
      rememberCsrfToken(refreshed.csrf_token);
      if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
        const token = currentCsrfToken();
        if (token) requestOptions.headers["X-CSRF-Token"] = token;
      }
      res = await fetch(url, requestOptions);
    }
  }

  if (res.status === 401) {
    throw new Error("Nicht eingeloggt");
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Anfrage fehlgeschlagen");
  }
  return res;
}

export async function login(username, password) {
  const res = await fetch(`${API_URL}/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Login fehlgeschlagen");
  }
  const payload = await res.json();
  rememberCsrfToken(payload.csrf_token);
  notifyAuthChanged();
  return payload;
}

export async function logout() {
  const headers = { "Content-Type": "application/json" };
  const token = await ensureCsrfToken();
  if (token) headers["X-CSRF-Token"] = token;
  await fetch(`${API_URL}/logout`, {
    method: "POST",
    credentials: "include",
    headers,
  });
  notifyAuthChanged();
}

export async function getUser() {
  const res = await fetchWithAuth(`${API_URL}/me`, { method: "GET" });
  return res.json();
}

export async function getUserList() {
  const res = await fetchWithAuth(`${API_URL}/user_list`, { method: "GET" });
  return res.json();
}

export async function updateUser(data) {
  const res = await fetchWithAuth(`${API_URL}/update_user`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function changePasswordByUser(data) {
  const res = await fetchWithAuth(`${API_URL}/change_password`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function adminGetAllUsers() {
  const res = await fetchWithAuth(`${API_URL}/admin/users`, { method: "GET" });
  return res.json();
}

export async function adminCreateUser(data) {
  const res = await fetchWithAuth(`${API_URL}/admin/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function adminUpdateUser(data) {
  const res = await fetchWithAuth(`${API_URL}/admin/users/${data.id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function adminDeactivateUser(userId) {
  const res = await fetchWithAuth(`${API_URL}/admin/users/${userId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
  });
  return res.json();
}

export async function adminActivateUser(userId) {
  const res = await fetchWithAuth(`${API_URL}/admin/users/${userId}/activate`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
  });
  return res.json();
}

export async function triggerSendPwResetToken(userId) {
  const res = await fetchWithAuth(`${API_URL}/admin/trigger_password_reset/${userId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
  });
  return res.json();
}

export async function getInstruments(view = "user") {
  const params = new URLSearchParams({ view });
  const res = await fetchWithAuth(`${API_URL}/admin/instruments?${params.toString()}`, { method: "GET" });
  return res.json();
}

export async function createInstrument(data) {
  const res = await fetchWithAuth(`${API_URL}/admin/instruments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function updateInstrument(instrumentId, data) {
  const res = await fetchWithAuth(`${API_URL}/admin/instruments/${instrumentId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function deleteInstrument(instrumentId) {
  const res = await fetchWithAuth(`${API_URL}/admin/instruments/${instrumentId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
  });
  return res.json();
}

export async function getGroups() {
  const res = await fetchWithAuth(`${API_URL}/admin/groups`, { method: "GET" });
  return res.json();
}

export async function createGroup(data) {
  const res = await fetchWithAuth(`${API_URL}/admin/groups`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function updateGroup(groupId, data) {
  const res = await fetchWithAuth(`${API_URL}/admin/groups/${groupId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function deleteGroup(groupId) {
  const res = await fetchWithAuth(`${API_URL}/admin/groups/${groupId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
  });
  return res.json();
}

export async function getAssignableUsers() {
  const res = await fetchWithAuth(`${API_URL}/admin/users/assignable`, { method: "GET" });
  return res.json();
}

export async function getUserGroups(userId) {
  const res = await fetchWithAuth(`${API_URL}/admin/users/${userId}/groups`, { method: "GET" });
  return res.json();
}

export async function updateUserGroups(userId, groupIds) {
  const res = await fetchWithAuth(`${API_URL}/admin/users/${userId}/groups`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ group_ids: groupIds }),
  });
  return res.json();
}

export async function getGroupInstruments(groupId) {
  const res = await fetchWithAuth(`${API_URL}/admin/groups/${groupId}/instruments`, { method: "GET" });
  return res.json();
}

export async function updateGroupInstruments(groupId, instrumentIds, notes = null) {
  const res = await fetchWithAuth(`${API_URL}/admin/groups/${groupId}/instruments`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instrument_ids: instrumentIds, notes }),
  });
  return res.json();
}

export async function getCollections() {
  const res = await fetchWithAuth(`${API_URL}/admin/collections`, { method: "GET" });
  return res.json();
}

export async function createCollection(data) {
  const res = await fetchWithAuth(`${API_URL}/admin/collections`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function updateCollection(collectionId, data) {
  const res = await fetchWithAuth(`${API_URL}/admin/collections/${collectionId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function deleteCollection(collectionId) {
  const res = await fetchWithAuth(`${API_URL}/admin/collections/${collectionId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
  });
  return res.json();
}

export async function getUserInstruments(userId) {
  const res = await fetchWithAuth(`${API_URL}/admin/users/${userId}/instruments`, { method: "GET" });
  return res.json();
}

export async function assignUserInstruments(userId, instrumentIds, notes = null) {
  const res = await fetchWithAuth(`${API_URL}/admin/users/${userId}/instruments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instrument_ids: instrumentIds, notes }),
  });
  return res.json();
}

export async function removeUserInstrument(userId, instrumentId) {
  const res = await fetchWithAuth(`${API_URL}/admin/users/${userId}/instruments/${instrumentId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
  });
  return res.json();
}

export async function getSongs(view = "editor") {
  const params = new URLSearchParams({ view });
  const res = await fetchWithAuth(`${API_URL}/admin/songs?${params.toString()}`, { method: "GET" });
  return res.json();
}

export async function createSong(data) {
  const res = await fetchWithAuth(`${API_URL}/admin/songs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function updateSong(songId, data) {
  const res = await fetchWithAuth(`${API_URL}/admin/songs/${songId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function deleteSong(songId) {
  const res = await fetchWithAuth(`${API_URL}/admin/songs/${songId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
  });
  return res.json();
}

export async function updateSongCollections(songId, collectionIds) {
  const res = await fetchWithAuth(`${API_URL}/admin/songs/${songId}/collections`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ collection_ids: collectionIds }),
  });
  return res.json();
}

export async function getScores(songId = null, view = "editor") {
  const params = new URLSearchParams({ view });
  if (songId !== null && songId !== undefined) params.set("song_id", String(songId));
  const url = `${API_URL}/admin/scores?${params.toString()}`;
  const res = await fetchWithAuth(url, { method: "GET" });
  return res.json();
}

export async function createScore(data) {
  const res = await fetchWithAuth(`${API_URL}/admin/scores`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function updateScore(scoreId, data) {
  const res = await fetchWithAuth(`${API_URL}/admin/scores/${scoreId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function deleteScore(scoreId) {
  const res = await fetchWithAuth(`${API_URL}/admin/scores/${scoreId}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
  });
  return res.json();
}

export async function uploadScorePdf(songId, file, options = {}) {
  const { instrumentId = null, instrumentName = null, instrumentTuning = null, notes = null } = options;
  const formData = new FormData();
  formData.append("song_id", String(songId));
  if (instrumentId !== null && instrumentId !== undefined) {
    formData.append("instrument_id", String(instrumentId));
  }
  if (instrumentName) {
    formData.append("instrument_name", instrumentName);
  }
  if (instrumentTuning) {
    formData.append("instrument_tuning", instrumentTuning);
  }
  formData.append("file", file);
  if (notes) formData.append("notes", notes);

  const res = await fetchWithAuth(`${API_URL}/admin/scores/upload`, {
    method: "POST",
    body: formData,
  });
  return res.json();
}

export async function previewScorePdfUpload(songId, file) {
  const formData = new FormData();
  formData.append("song_id", String(songId));
  formData.append("file", file);
  const res = await fetchWithAuth(`${API_URL}/admin/scores/upload/preview`, {
    method: "POST",
    body: formData,
  });
  return res.json();
}

export async function previewNewSongPdfUpload(file) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetchWithAuth(`${API_URL}/admin/songs/upload/preview`, {
    method: "POST",
    body: formData,
  });
  return res.json();
}

export async function commitScorePdfUpload(data) {
  const res = await fetchWithAuth(`${API_URL}/admin/scores/upload/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function commitNewSongPdfUpload(data) {
  const res = await fetchWithAuth(`${API_URL}/admin/songs/upload/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function getStorageStatus() {
  const res = await fetchWithAuth(`${API_URL}/admin/storage/status`, { method: "GET" });
  return res.json();
}

export async function rescrapeStorage() {
  const res = await fetchWithAuth(`${API_URL}/admin/storage/rescrape`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return res.json();
}

export function getScoreDocumentUrl(scoreId) {
  return `${API_URL}/scores/${scoreId}/document`;
}

export async function verifyPwResetToken(resetToken) {
  const res = await fetch(`${API_URL}/password_reset/verify_reset_token`, {
    method: "GET",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${resetToken}`,
    },
  });
  if (!res.ok) throw new Error("Ungültiger oder abgelaufener Reset-Token");
  return res.json();
}

export async function resetPassword(resetToken, newPassword) {
  const res = await fetch(`${API_URL}/password_reset/new_password`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${resetToken}`,
    },
    body: JSON.stringify({ new_password: newPassword }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Passwort zurücksetzen fehlgeschlagen");
  }
  return res.json();
}
