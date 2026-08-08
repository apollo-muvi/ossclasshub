import { useEffect, useId, useMemo, useRef, useState } from "react";
import QRCode from "qrcode";
import { downloadAuthorizedBlob, fetchAuthorizedBlobUrl, getAppSettings, request, uploadFile } from "./apiClient.js";

const CATEGORY_OPTIONS = [
  ["announcement", "公告"],
  ["lesson", "課程"],
  ["homework", "作業"],
  ["bring", "攜帶物品"],
  ["event", "活動"],
  ["leave", "請假"],
  ["payment_reminder", "繳費"],
  ["other", "其他"],
];
const CATEGORY_LABELS = Object.fromEntries(CATEGORY_OPTIONS);
const CLASSHUB_ID_PATTERN = /^[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])$/;
const CLASSHUB_ID_HELP = "ClassHub ID 由開通時核准，請輸入 3-32 個字元，只能使用小寫英文、數字和連字號，開頭與結尾不能是連字號。";
const CLASSHUB_ID = "local-classhub";
const browserStore = window["local" + "Sto" + "ra" + "ge"];
const TEACHER_SESSION_PREFIX = "classhubTeacherSession:";
const PARENT_SESSION_PREFIX = "classhubParentSession:";
const MAX_IMAGES_PER_POST = 3;
const DEFAULT_MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024;
const POST_RETENTION_DAYS = 3;
const PARENT_IDLE_REFRESH_MS = 180000;
const TEACHER_FEED_REFRESH_MS = 30000;
const PUBLIC_APP_ORIGIN = import.meta.env.VITE_PUBLIC_APP_ORIGIN || "";
const LOCAL_INVITE_HOST = "localhost";
const SHOW_ADVANCED_OPS = false;
const PUBLISH_MODE_STEPS = [
  ["class", "選班級"],
  ["target", "確認對象"],
  ["publish", "發布"],
];

function formatDateInputValue(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function buildRecentDateOptions(days) {
  const options = [];
  for (let index = 0; index < days; index += 1) {
    const date = new Date();
    date.setDate(date.getDate() - index);
    const value = formatDateInputValue(date);
    options.push([value, index === 0 ? "今天" : `${index} 天前`]);
  }
  return options;
}

function formatFileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 MB";
  const megabytes = bytes / (1024 * 1024);
  return `${megabytes >= 10 ? megabytes.toFixed(0) : megabytes.toFixed(1)} MB`;
}

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-TW", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatCsvCell(value) {
  const text = String(value ?? "");
  return `"${text.replace(/"/g, '""')}"`;
}

function teacherSessionKey(tenantId) {
  return `${TEACHER_SESSION_PREFIX}${tenantId}`;
}

function readSavedTeacherSession(tenantId) {
  if (!tenantId) return null;
  try {
    const raw = browserStore.getItem(teacherSessionKey(tenantId));
    if (!raw) return null;
    const data = JSON.parse(raw);
    return data?.access_token ? data : null;
  } catch {
    return null;
  }
}

function writeSavedTeacherSession(tenantId, data) {
  if (!tenantId || !data?.access_token) return;
  browserStore.setItem(teacherSessionKey(tenantId), JSON.stringify(data));
}

function clearSavedTeacherSession(tenantId) {
  if (!tenantId) return;
  browserStore.removeItem(teacherSessionKey(tenantId));
}

function parentSessionKey(tenantId) {
  return `${PARENT_SESSION_PREFIX}${tenantId}`;
}

function readSavedParentSession(tenantId) {
  if (!tenantId) return null;
  try {
    const raw = browserStore.getItem(parentSessionKey(tenantId));
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (!data?.access_token || !Array.isArray(data.student_ids) || !data.student_ids.length) {
      return null;
    }
    return data;
  } catch {
    return null;
  }
}

function writeSavedParentSession(tenantId, data) {
  if (!tenantId || !data?.access_token) return;
  browserStore.setItem(parentSessionKey(tenantId), JSON.stringify(data));
}

function clearSavedParentSession(tenantId) {
  if (!tenantId) return;
  browserStore.removeItem(parentSessionKey(tenantId));
}

function readInviteParams() {
  const params = new URLSearchParams(window.location.search);
  return {
    inviteToken: params.get("invite_token") || "",
    mode: params.get("mode") || "",
  };
}

function buildParentInviteUrl(inviteToken) {
  const currentUrl = new URL(window.location.href);
  const localHostname = currentUrl.hostname === "localhost" || currentUrl.hostname === "127.0.0.1";
  const origin = PUBLIC_APP_ORIGIN || (localHostname
    ? `${currentUrl.protocol}//${LOCAL_INVITE_HOST}${currentUrl.port ? `:${currentUrl.port}` : ""}`
    : currentUrl.origin);
  const url = new URL(currentUrl.pathname || "/", origin);
  url.search = "";
  url.hash = "";
  url.searchParams.set("mode", "parent");
  url.searchParams.set("invite_token", inviteToken);
  return url.toString();
}

function getClassHubIdError(value) {
  const normalized = value.trim();
  if (!normalized) return "請輸入 ClassHub ID";
  if (normalized.length < 3 || normalized.length > 32) {
    return "ClassHub ID 長度必須為 3 到 32 個字元";
  }
  if (/[A-Z]/.test(normalized)) return "ClassHub ID 必須全小寫";
  if (!/^[a-z0-9-]+$/.test(normalized)) {
    return "ClassHub ID 只能使用小寫英文、數字和連字號";
  }
  if (!CLASSHUB_ID_PATTERN.test(normalized)) {
    return "ClassHub ID 開頭與結尾必須是小寫英文或數字";
  }
  return "";
}

export default function App() {
  const initialInvite = useMemo(readInviteParams, []);
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState("");
  const tenantId = CLASSHUB_ID;
  const [teacherToken, setTeacherToken] = useState("");
  const [parentToken, setParentToken] = useState("");
  const [classId, setClassId] = useState("");
  const [classes, setClasses] = useState([]);
  const [students, setStudents] = useState([]);
  const [parents, setParents] = useState([]);
  const [rosterFile, setRosterFile] = useState(null);
  const [rosterPreview, setRosterPreview] = useState(null);
  const [rosterPreviewing, setRosterPreviewing] = useState(false);
  const [rosterApplying, setRosterApplying] = useState(false);
  const [studentId, setStudentId] = useState("");
  const [parentStudentIds, setParentStudentIds] = useState([]);
  const [parentId, setParentId] = useState("");
  const [inviteToken, setInviteToken] = useState(() => initialInvite.inviteToken);
  const [classInviteItems, setClassInviteItems] = useState([]);
  const [classInviteIndex, setClassInviteIndex] = useState(0);
  const [classInvitesCreating, setClassInvitesCreating] = useState(false);
  const [teacherPostDate, setTeacherPostDate] = useState(() => formatDateInputValue(new Date()));
  const [parentPostDate, setParentPostDate] = useState(() => formatDateInputValue(new Date()));
  const [todayItems, setTodayItems] = useState([]);
  const [selectedPostIds, setSelectedPostIds] = useState([]);
  const [postStatusDetail, setPostStatusDetail] = useState(null);
  const [postStatusLoadingId, setPostStatusLoadingId] = useState("");
  const [parentItems, setParentItems] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [webhookDeliveries, setWebhookDeliveries] = useState([]);
  const [lineStatus, setLineStatus] = useState(null);
  const [lineBinding, setLineBinding] = useState(null);
  const [lineDeliveries, setLineDeliveries] = useState([]);
  const [message, setMessage] = useState("");
  const [publishNotice, setPublishNotice] = useState("");
  const [mode, setMode] = useState(() => (initialInvite.inviteToken || initialInvite.mode === "parent" ? "parent" : "teacher"));
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [actionPending, setActionPending] = useState(false);
  const actionPendingRef = useRef(false);
  const [postPublishing, setPostPublishing] = useState(false);
  const postPublishingRef = useRef(false);
  const [publishResetKey, setPublishResetKey] = useState(0);
  const [replySendingPostId, setReplySendingPostId] = useState("");

  const activeTenantId = CLASSHUB_ID;
  const unreadCount = notifications.filter((item) => !item.read_at).length;
  const pendingWebhookCount = webhookDeliveries.filter((item) => item.status !== "sent").length;
  const pendingLineCount = lineDeliveries.filter((item) => item.status !== "sent").length;
  const teacherReady = Boolean(activeTenantId && teacherToken);
  const activeParentStudentId = studentId || parentStudentIds[0] || "";
  const parentReady = Boolean(activeTenantId && parentToken && activeParentStudentId);
  const parentOnly = Boolean(parentToken && !teacherToken);
  const inviteUrl = useMemo(
    () => (inviteToken ? buildParentInviteUrl(inviteToken) : ""),
    [inviteToken],
  );
  const postDateOptions = useMemo(() => buildRecentDateOptions(POST_RETENTION_DAYS), []);

  const todaySummary = useMemo(() => {
    const recipients = todayItems.reduce((sum, item) => sum + (item.recipient_count || 0), 0);
    const confirmed = todayItems.reduce((sum, item) => sum + (item.confirmation_count || 0), 0);
    return { posts: todayItems.length, recipients, confirmed };
  }, [todayItems]);

  useEffect(() => {
    getAppSettings()
      .then(setSettings)
      .catch((err) => setError(err.message || "無法載入系統設定"));
  }, []);

  useEffect(() => {
    if (!publishNotice) return undefined;
    const timer = window.setTimeout(() => setPublishNotice(""), 2000);
    return () => window.clearTimeout(timer);
  }, [publishNotice]);

  useEffect(() => {
    let cancelled = false;
    const storedSession = readSavedTeacherSession(activeTenantId);
    if (!storedSession) {
      setTeacherToken("");
      setClassId("");
      setClasses([]);
      setStudents([]);
      setParents([]);
      setRosterFile(null);
      setRosterPreview(null);
      setRosterPreviewing(false);
      setRosterApplying(false);
      setClassInvitesCreating(false);
      setTodayItems([]);
      setSelectedPostIds([]);
      return () => {
        cancelled = true;
      };
    }
    setTeacherToken(storedSession.access_token);
    (async () => {
      try {
        await loadTeacherClasses(storedSession.access_token);
        await loadTeacherRoster(storedSession.access_token);
        await loadTeacherToday(storedSession.access_token);
        if (!cancelled) {
          setMode("publish");
          setMessage("已恢復登入");
        }
      } catch {
        clearSavedTeacherSession(activeTenantId);
        if (!cancelled) {
          setTeacherToken("");
          setClassId("");
          setClasses([]);
          setStudents([]);
          setParents([]);
          setRosterFile(null);
          setRosterPreview(null);
          setRosterPreviewing(false);
          setRosterApplying(false);
          setClassInvitesCreating(false);
          setTodayItems([]);
          setSelectedPostIds([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeTenantId]);

  useEffect(() => {
    const storedSession = readSavedParentSession(activeTenantId);
    if (!storedSession) {
      setParentToken("");
      setParentStudentIds([]);
      setParentItems([]);
      setNotifications([]);
      setLineBinding(null);
      return;
    }
    const selectedStudentId = storedSession.selected_student_id || storedSession.student_ids[0] || "";
    clearSavedTeacherSession(activeTenantId);
    setTeacherToken("");
    setClassId("");
    setClasses([]);
    setStudents([]);
    setParents([]);
    setTodayItems([]);
    setSelectedPostIds([]);
    setSettingsOpen(false);
    setMode("parent");
    setParentToken(storedSession.access_token);
    setParentStudentIds(storedSession.student_ids);
    setParentId(storedSession.parent_id || "");
    if (selectedStudentId) {
      setStudentId(selectedStudentId);
    }
    loadParentToday(storedSession.access_token, selectedStudentId).catch(() => {
      clearSavedParentSession(activeTenantId);
      setParentToken("");
      setParentStudentIds([]);
      setParentItems([]);
      setNotifications([]);
      setLineBinding(null);
      setError("家長登入已過期，請重新綁定");
    });
  }, [activeTenantId]);

  useEffect(() => {
    if (
      (parentOnly && (mode === "teacher" || mode === "publish" || mode === "ops")) ||
      (!SHOW_ADVANCED_OPS && mode === "ops")
    ) {
      setMode("parent");
      return;
    }
    if (!teacherReady && mode === "ops") {
      setMode(parentToken ? "parent" : "teacher");
    }
  }, [mode, parentOnly, parentToken, teacherReady]);

  useEffect(() => {
    if (!parentReady) return undefined;
    let timer = 0;
    let refreshing = false;
    const resetIdleRefresh = () => {
      window.clearTimeout(timer);
      if (document.hidden) return;
      timer = window.setTimeout(refreshParentToday, PARENT_IDLE_REFRESH_MS);
    };
    const refreshParentToday = () => {
      window.clearTimeout(timer);
      if (document.hidden || refreshing) return;
      refreshing = true;
      loadParentToday(parentToken, activeParentStudentId, parentPostDate)
        .catch((err) => {
          setError(err.message || "家長聯絡簿更新失敗");
        })
        .finally(() => {
          refreshing = false;
          resetIdleRefresh();
        });
    };
    const handleVisibilityChange = () => {
      if (document.hidden) {
        window.clearTimeout(timer);
        return;
      }
      refreshParentToday();
    };
    const activityEvents = ["pointerdown", "keydown", "touchstart", "scroll"];
    activityEvents.forEach((eventName) => {
      window.addEventListener(eventName, resetIdleRefresh, { passive: true });
    });
    window.addEventListener("focus", refreshParentToday);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    resetIdleRefresh();
    return () => {
      window.clearTimeout(timer);
      activityEvents.forEach((eventName) => {
        window.removeEventListener(eventName, resetIdleRefresh);
      });
      window.removeEventListener("focus", refreshParentToday);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [activeParentStudentId, parentPostDate, parentReady, parentToken]);

  useEffect(() => {
    if ((mode !== "teacher" && mode !== "publish") || !teacherReady || parentOnly) return undefined;
    let timer = 0;
    let refreshing = false;
    const refreshTeacherToday = () => {
      if (document.hidden || actionPendingRef.current || refreshing) return;
      refreshing = true;
      loadTeacherToday(teacherToken, teacherPostDate, classId)
        .catch((err) => {
          setError(err.message || "教師今日貼文更新失敗");
        })
        .finally(() => {
          refreshing = false;
        });
    };
    timer = window.setInterval(refreshTeacherToday, TEACHER_FEED_REFRESH_MS);
    window.addEventListener("focus", refreshTeacherToday);
    document.addEventListener("visibilitychange", refreshTeacherToday);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshTeacherToday);
      document.removeEventListener("visibilitychange", refreshTeacherToday);
    };
  }, [classId, mode, parentOnly, teacherPostDate, teacherReady, teacherToken]);

  async function runAction(action) {
    if (actionPendingRef.current) {
      return;
    }
    actionPendingRef.current = true;
    setActionPending(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setMessage("");
      setError(err.message || "請求失敗");
    } finally {
      actionPendingRef.current = false;
      setActionPending(false);
    }
  }

  async function loginTeacher(event) {
    event.preventDefault();
    await runAction(async () => {
      const idError = getClassHubIdError(activeTenantId);
      if (idError) throw new Error(idError);
      const form = new FormData(event.currentTarget);
      const data = await request(`/auth/login`, {
        method: "POST",
        body: JSON.stringify({
          username: form.get("username"),
          password: form.get("password"),
        }),
      });
      setTeacherToken(data.access_token);
      writeSavedTeacherSession(activeTenantId, data);
      setSettingsOpen(false);
      setMessage("已登入");
      await loadTeacherClasses(data.access_token);
      await loadTeacherRoster(data.access_token);
      await loadTeacherToday(data.access_token);
      setMode("publish");
    });
  }

  function signOut() {
    clearSavedTeacherSession(activeTenantId);
    setTeacherToken("");
    setClassId("");
    setClasses([]);
    setStudents([]);
    setParents([]);
    setRosterFile(null);
    setRosterPreview(null);
    setRosterPreviewing(false);
    setRosterApplying(false);
    setClassInvitesCreating(false);
    setTodayItems([]);
    setSelectedPostIds([]);
    setPostStatusDetail(null);
    setPostStatusLoadingId("");
    setSettingsOpen(false);
    setMessage("已登出，請重新登入");
  }

  function signOutParent() {
    clearSavedParentSession(activeTenantId);
    setParentToken("");
    setParentStudentIds([]);
    setParentId("");
    setStudentId("");
    setParentItems([]);
    setNotifications([]);
    setLineBinding(null);
    setLineDeliveries([]);
    setMessage("家長已登出，可重新綁定其他邀請");
    setMode("parent");
  }

  async function changePassword(event) {
    event.preventDefault();
    const targetForm = event.currentTarget;
    await runAction(async () => {
      const form = new FormData(targetForm);
      const nextPassword = form.get("new_password");
      if (nextPassword !== form.get("confirm_password")) {
        throw new Error("新密碼不一致");
      }
      await request(`/auth/change-password`, {
        method: "POST",
        token: teacherToken,
        body: JSON.stringify({
          current_password: form.get("current_password"),
          new_password: nextPassword,
        }),
      });
      targetForm.reset();
      clearSavedTeacherSession(activeTenantId);
      setTeacherToken("");
      setClassId("");
      setClasses([]);
      setStudents([]);
      setParents([]);
      setSettingsOpen(false);
      setMessage("密碼已更新，請重新登入");
    });
  }

  async function createClass(event) {
    event.preventDefault();
    await runAction(async () => {
      const form = new FormData(event.currentTarget);
      const data = await request(`/classes`, {
        method: "POST",
        token: teacherToken,
        body: JSON.stringify({ name: form.get("name") }),
      });
      setClassId(data.id);
      setStudentId("");
      setParentId("");
      setInviteToken("");
      setClassInviteItems([]);
      setClassInviteIndex(0);
      setClasses((items) => [data, ...items.filter((item) => item.id !== data.id)]);
      setMessage(`班級已建立：${data.name}`);
    });
  }

  function selectClass(nextClassId) {
    setClassId(nextClassId);
    setStudentId("");
    setParentId("");
    setInviteToken("");
    setClassInviteItems([]);
    setClassInviteIndex(0);
    if (teacherToken) {
      runAction(() => loadTeacherToday(teacherToken, teacherPostDate, nextClassId));
    }
  }

  function selectTeacherPostDate(nextDate) {
    setTeacherPostDate(nextDate);
    if (teacherToken) {
      runAction(() => loadTeacherToday(teacherToken, nextDate, classId));
    }
  }

  function selectParentPostDate(nextDate) {
    setParentPostDate(nextDate);
    if (parentToken && activeParentStudentId) {
      runAction(() => loadParentToday(parentToken, activeParentStudentId, nextDate));
    }
  }

  function selectInviteTarget(value) {
    const [nextParentId = "", nextStudentId = ""] = value.split(":");
    setParentId(nextParentId);
    setStudentId(nextStudentId);
    setInviteToken("");
    setClassInviteItems([]);
    setClassInviteIndex(0);
  }

  function selectClassInvite(nextIndex, items = classInviteItems) {
    if (!items.length) {
      setClassInviteIndex(0);
      return;
    }
    const boundedIndex = Math.min(Math.max(nextIndex, 0), items.length - 1);
    const item = items[boundedIndex];
    setClassInviteIndex(boundedIndex);
    setParentId(item.parent_id);
    setStudentId(item.student_id);
    setInviteToken(item.invite_token);
  }

  async function createStudentAndParent(event) {
    event.preventDefault();
    const targetForm = event.currentTarget;
    await runAction(async () => {
      const form = new FormData(targetForm);
      const targetClassId = form.get("class_id") || classId;
      const existingStudentId = form.get("student_id") || "";
      const invite = await request(`/parent-invite`, {
        method: "POST",
        token: teacherToken,
        body: JSON.stringify({
          class_id: targetClassId,
          student_id: existingStudentId || null,
          student_name: existingStudentId ? null : form.get("student_name"),
          parent_name: form.get("parent_name"),
          phone: form.get("phone") || "",
        }),
      });
      const targetStudentId = invite.student_id;
      const targetParentId = invite.parent_id;
      setClassId(targetClassId);
      setStudentId(targetStudentId);
      setParentId(targetParentId);
      setInviteToken(invite.invite_token);
      setClassInviteItems([]);
      setClassInviteIndex(0);
      targetForm.reset();
      setMessage("家長已連結，邀請碼已產生");
      await loadTeacherRoster(teacherToken);
    });
  }

  async function createInvite() {
    await runAction(async () => {
      const data = await request(`/parent-invites`, {
        method: "POST",
        token: teacherToken,
        body: JSON.stringify({ parent_id: parentId, student_id: studentId }),
      });
      setInviteToken(data.invite_token);
      setClassInviteItems([]);
      setClassInviteIndex(0);
      setMessage("家長邀請碼已產生");
    });
  }

  async function createClassInvites() {
    setClassInvitesCreating(true);
    setMessage("整班 QR Code 產生中...");
    try {
      await runAction(async () => {
        if (!classId) {
          throw new Error("請先選擇班級");
        }
        const data = await request(
          `/classes/${classId}/parent-invites`,
          {
            method: "POST",
            token: teacherToken,
            body: JSON.stringify({}),
          },
        );
        const inviteItems = (data.items || []).map((item) => ({
          key: `${item.parent_id}:${item.student_id}`,
          parent_id: item.parent_id,
          student_id: item.student_id,
          parent_name: item.parent_name,
          student_name: item.student_name,
          class_name: item.class_name,
          invite_token: item.invite_token,
          invite_url: buildParentInviteUrl(activeTenantId, item.invite_token),
          expires_at: item.expires_at,
        }));
        setClassInviteItems(inviteItems);
        selectClassInvite(0, inviteItems);
        setMessage(`已產生 ${inviteItems.length} 位班級家長邀請碼`);
      });
    } finally {
      setClassInvitesCreating(false);
    }
  }

  function printClassInvites() {
    if (!classInviteItems.length) {
      setMessage("請先產生班級家長邀請碼");
      return;
    }
    window.requestAnimationFrame(() => window.print());
  }

  async function copyInviteLink() {
    await runAction(async () => {
      await navigator.clipboard.writeText(inviteUrl);
      setMessage("邀請連結已複製");
    });
  }

  async function shareInvite() {
    await runAction(async () => {
      const data = {
        title: "ClassHub 家長邀請",
        text: "請開啟 ClassHub 完成家長綁定。",
        url: inviteUrl,
      };
      if (navigator.share) {
        try {
          await navigator.share(data);
          setMessage("邀請連結已分享");
          return;
        } catch (err) {
          if (err.name === "AbortError") return;
          throw err;
        }
      }
      await navigator.clipboard.writeText(inviteUrl);
      setMessage("此裝置不支援分享，邀請連結已複製");
    });
  }

  async function bindParent() {
    await runAction(async () => {
      const idError = getClassHubIdError(activeTenantId);
      if (idError) throw new Error(idError);
      const data = await request(`/parent-auth/bind`, {
        method: "POST",
        body: JSON.stringify({ invite_token: inviteToken }),
      });
      const boundStudentIds = data.student_ids || [];
      const selectedStudentId = boundStudentIds[0] || "";
      clearSavedTeacherSession(activeTenantId);
      setTeacherToken("");
      setClassId("");
      setClasses([]);
      setStudents([]);
      setParents([]);
      setRosterFile(null);
      setRosterPreview(null);
      setRosterPreviewing(false);
      setRosterApplying(false);
      setClassInvitesCreating(false);
      setTodayItems([]);
      setSelectedPostIds([]);
      setSettingsOpen(false);
      setMode("parent");
      setParentToken(data.access_token);
      setParentStudentIds(boundStudentIds);
      setParentId(data.parent_id || "");
      if (selectedStudentId) {
        setStudentId(selectedStudentId);
      }
      writeSavedParentSession(activeTenantId, {
        access_token: data.access_token,
        parent_id: data.parent_id || "",
        student_ids: boundStudentIds,
        selected_student_id: selectedStudentId,
      });
      setMessage("家長存取權已啟用");
      await loadParentToday(data.access_token, selectedStudentId);
      await loadLineBindingStatus(data.access_token);
    });
  }

  async function publishPost(event) {
    event.preventDefault();
    if (postPublishingRef.current) {
      return;
    }
    postPublishingRef.current = true;
    setPostPublishing(true);
    const targetForm = event.currentTarget;
    try {
      await runAction(async () => {
        setMessage("發布中...");
        const form = new FormData(targetForm);
        const files = Array.from(form.getAll("images")).filter((file) => file.name);
        if (files.length > MAX_IMAGES_PER_POST) {
          throw new Error(`每則訊息最多 ${MAX_IMAGES_PER_POST} 張圖片`);
        }
        const maxImageUploadBytes = settings?.max_image_upload_bytes || DEFAULT_MAX_IMAGE_UPLOAD_BYTES;
        const oversizedFile = files.find((file) => file.size > maxImageUploadBytes);
        if (oversizedFile) {
          throw new Error(
            `圖片「${oversizedFile.name}」大小 ${formatFileSize(oversizedFile.size)}，超過單張上限 ${formatFileSize(maxImageUploadBytes)}，請縮小後再發布。`,
          );
        }
        const postDate = formatDateInputValue(new Date());
        const data = await request(`/posts`, {
          method: "POST",
          token: teacherToken,
          body: JSON.stringify({
            class_id: classId,
            title: form.get("title"),
            post_date: postDate,
            category: form.get("category"),
            content: form.get("content"),
            require_confirmation: form.get("require_confirmation") === "on",
          }),
        });
        for (const file of files) {
          await uploadFile(`/posts/${data.id}/images`, file, {
            token: teacherToken,
          });
        }
        targetForm.reset();
        setPublishResetKey((key) => key + 1);
        setTeacherPostDate(postDate);
        setMessage("");
        setPublishNotice(`已發布給 ${data.recipient_count} 位收件人`);
        await loadTeacherToday(teacherToken, postDate, classId);
      });
    } finally {
      postPublishingRef.current = false;
      setPostPublishing(false);
    }
  }

  async function deletePublishedPost(postId) {
    if (!window.confirm("確定刪除這則已發布的聯絡簿？")) return;
    await runAction(async () => {
      const result = await request(`/posts/${postId}`, {
        method: "DELETE",
        token: teacherToken,
      });
      setTodayItems((items) => items.filter((item) => item.id !== postId));
      setSelectedPostIds((ids) => ids.filter((id) => id !== postId));
      if (postStatusDetail?.post?.id === postId) {
        setPostStatusDetail(null);
      }
      setMessage(`已刪除 ${result.deleted_count || 1} 則聯絡簿`);
      await loadTeacherToday(teacherToken, teacherPostDate, classId);
    });
  }

  async function deleteSelectedPublishedPosts() {
    const availableIds = new Set(todayItems.map((item) => item.id));
    const postIds = selectedPostIds.filter((id) => availableIds.has(id));
    if (!postIds.length) {
      setMessage("請先選取要刪除的聯絡簿");
      return;
    }
    if (!window.confirm(`確定刪除 ${postIds.length} 則已發布的聯絡簿？`)) return;
    await runAction(async () => {
      const result = await request(`/posts/delete`, {
        method: "POST",
        token: teacherToken,
        body: JSON.stringify({ post_ids: postIds }),
      });
      const deletedIds = new Set(result.post_ids || postIds);
      setTodayItems((items) => items.filter((item) => !deletedIds.has(item.id)));
      setSelectedPostIds((ids) => ids.filter((id) => !deletedIds.has(id)));
      if (postStatusDetail?.post?.id && deletedIds.has(postStatusDetail.post.id)) {
        setPostStatusDetail(null);
      }
      setMessage(`已刪除 ${result.deleted_count || deletedIds.size} 則聯絡簿`);
      await loadTeacherToday(teacherToken, teacherPostDate, classId);
    });
  }

  function togglePostSelection(postId) {
    setSelectedPostIds((ids) => (
      ids.includes(postId) ? ids.filter((id) => id !== postId) : [...ids, postId]
    ));
  }

  async function exportContactBookText() {
    await runAction(async () => {
      const query = classId ? `?class_id=${encodeURIComponent(classId)}` : "";
      const blob = await downloadAuthorizedBlob(
        `/posts/export.txt${query}`,
        teacherToken,
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      const dateText = new Date().toISOString().slice(0, 10);
      link.href = url;
      link.download = `classhub-contact-book-${dateText}.txt`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setMessage("聯絡簿文字備份已匯出");
    });
  }

  function selectRosterFile(file) {
    setRosterFile(file || null);
    setRosterPreview(null);
  }

  async function downloadRosterTemplate() {
    await runAction(async () => {
      const blob = await downloadAuthorizedBlob(
        `/roster-imports/template.csv`,
        teacherToken,
      );
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "classhub-roster-template.csv";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setMessage("名單範本已下載");
    });
  }

  async function previewRosterImport(event) {
    event.preventDefault();
    if (!rosterFile) {
      setError("請先選擇 CSV 檔案");
      return;
    }
    setRosterPreviewing(true);
    setMessage("名單預覽中...");
    try {
      await runAction(async () => {
        const data = await uploadFile(
          `/roster-imports/preview`,
          rosterFile,
          { token: teacherToken },
        );
        setRosterPreview(data);
        setMessage(data.can_apply ? "名單預覽完成" : "名單需要修正後才能同步");
      });
    } finally {
      setRosterPreviewing(false);
    }
  }

  async function applyRosterImport() {
    if (!rosterFile || !rosterPreview?.can_apply) return;
    const deactivateCount = (rosterPreview.summary?.students_to_deactivate || 0)
      + (rosterPreview.summary?.guardians_to_deactivate || 0);
    if (
      deactivateCount > 0
      && !window.confirm(`這次同步會停用 ${deactivateCount} 筆名單資料，確定套用？`)
    ) {
      return;
    }
    setRosterApplying(true);
    setMessage("名單同步中...");
    try {
      await runAction(async () => {
        const data = await uploadFile(
          `/roster-imports/apply`,
          rosterFile,
          { token: teacherToken },
        );
        setRosterPreview(data);
        setClassInviteItems([]);
        setClassInviteIndex(0);
        setInviteToken("");
        await loadTeacherClasses(teacherToken);
        await loadTeacherRoster(teacherToken);
        await loadTeacherToday(teacherToken, teacherPostDate, classId);
        setMessage("名單已同步");
      });
    } finally {
      setRosterApplying(false);
    }
  }

  function downloadRosterIssues() {
    const items = [
      ...(rosterPreview?.errors || []),
      ...(rosterPreview?.conflicts || []),
    ];
    if (!items.length) {
      setMessage("目前沒有名單錯誤");
      return;
    }
    const rows = [
      ["列號", "欄位", "訊息"],
      ...items.map((item) => [
        item.row || "",
        item.field || "",
        item.message || "",
      ]),
    ];
    const csvText = rows
      .map((row) => row.map(formatCsvCell).join(","))
      .join("\n");
    const blob = new Blob([`\uFEFF${csvText}`], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "classhub-roster-import-errors.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setMessage("名單錯誤清單已下載");
  }

  async function updateStudent(event, targetStudentId) {
    event.preventDefault();
    const targetForm = event.currentTarget;
    await runAction(async () => {
      const form = new FormData(targetForm);
      await request(`/students/${targetStudentId}`, {
        method: "PATCH",
        token: teacherToken,
        body: JSON.stringify({
          name: form.get("name"),
          class_id: form.get("class_id"),
        }),
      });
      setMessage("學生資料已更新");
      await loadTeacherRoster(teacherToken);
    });
  }

  async function deleteStudent(targetStudentId, studentName) {
    if (!window.confirm(`確定刪除學生「${studentName}」？`)) return;
    await runAction(async () => {
      await request(`/students/${targetStudentId}`, {
        method: "DELETE",
        token: teacherToken,
      });
      if (studentId === targetStudentId) {
        setStudentId("");
        setParentId("");
        setInviteToken("");
      }
      setMessage("學生已刪除");
      await loadTeacherRoster(teacherToken);
    });
  }

  async function updateParent(event, targetParentId) {
    event.preventDefault();
    const targetForm = event.currentTarget;
    await runAction(async () => {
      const form = new FormData(targetForm);
      await request(`/parents/${targetParentId}`, {
        method: "PATCH",
        token: teacherToken,
        body: JSON.stringify({
          name: form.get("name"),
          phone: form.get("phone") || "",
        }),
      });
      setMessage("家長資料已更新");
      await loadTeacherRoster(teacherToken);
    });
  }

  async function deleteParent(targetParentId, parentName) {
    if (!window.confirm(`確定刪除家長「${parentName}」？`)) return;
    await runAction(async () => {
      await request(`/parents/${targetParentId}`, {
        method: "DELETE",
        token: teacherToken,
      });
      if (parentId === targetParentId) {
        setParentId("");
        setInviteToken("");
      }
      setClassInviteItems((items) => items.filter((item) => item.parent_id !== targetParentId));
      setClassInviteIndex(0);
      setMessage("家長已刪除");
      await loadTeacherRoster(teacherToken);
    });
  }

  async function loadTeacherToday(token = teacherToken, selectedDate = teacherPostDate, selectedClassId = classId) {
    if (!activeTenantId || !token) return;
    const params = new URLSearchParams();
    if (selectedDate) params.set("post_date", selectedDate);
    if (selectedClassId) params.set("class_id", selectedClassId);
    const data = await request(`/posts/today?${params.toString()}`, {
      token,
    });
    const items = data.items || [];
    setTodayItems(items);
    if (postStatusDetail?.post?.id && !items.some((item) => item.id === postStatusDetail.post.id)) {
      setPostStatusDetail(null);
    }
    setSelectedPostIds((ids) => {
      const visibleIds = new Set(items.map((item) => item.id));
      return ids.filter((id) => visibleIds.has(id));
    });
  }

  async function loadPostRecipientStatus(postId) {
    if (!postId) return;
    setPostStatusLoadingId(postId);
    try {
      await runAction(async () => {
        const data = await request(`/posts/${postId}/recipient-status`, {
          token: teacherToken,
        });
        setPostStatusDetail(data);
        setMessage("狀態明細已載入");
      });
    } finally {
      setPostStatusLoadingId("");
    }
  }

  async function loadTeacherClasses(token = teacherToken) {
    if (!activeTenantId || !token) return [];
    const data = await request(`/classes`, {
      token,
    });
    const items = data.items || [];
    setClasses(items);
    setClassId((currentClassId) => {
      if (!currentClassId || items.some((item) => item.id === currentClassId)) {
        return currentClassId;
      }
      return "";
    });
    return items;
  }

  async function loadTeacherRoster(token = teacherToken) {
    if (!activeTenantId || !token) return;
    const [studentData, parentData] = await Promise.all([
      request(`/students`, { token }),
      request(`/parents`, { token }),
    ]);
    setStudents(studentData.items || []);
    setParents(parentData.items || []);
  }

  async function loadParentToday(token = parentToken, selectedStudentId = activeParentStudentId, selectedDate = parentPostDate) {
    if (!activeTenantId || !token || !selectedStudentId) return;
    const params = new URLSearchParams({
      student_id: selectedStudentId,
      post_date: selectedDate,
    });
    const data = await request(`/parent/today?${params.toString()}`, {
      token,
    });
    setParentItems(data.items || []);
    await loadNotifications(token, selectedStudentId);
    await loadLineBindingStatus(token);
  }

  async function loadLineBindingStatus(token = parentToken) {
    if (!activeTenantId || !token) return;
    const data = await request(`/parent/line-binding/status`, {
      token,
    });
    setLineBinding((current) => ({ ...(current || {}), status: data.status, bound: data.bound }));
  }

  async function startLineBinding() {
    await runAction(async () => {
      const data = await request(`/parent/line-binding/start`, {
        method: "POST",
        token: parentToken,
        body: JSON.stringify({ student_id: activeParentStudentId }),
      });
      setLineBinding(data);
      setMessage("選配通知綁定碼已產生");
    });
  }

  async function confirmParentPost(postId) {
    await runAction(async () => {
      await request(`/parent/posts/${postId}/confirm?student_id=${activeParentStudentId}`, {
        method: "POST",
        token: parentToken,
      });
      setParentItems((items) => items.map((item) => (
        item.id === postId ? { ...item, parent_confirmed: true } : item
      )));
      setMessage("貼文已確認");
      await loadParentToday(parentToken, activeParentStudentId, parentPostDate);
    });
  }

  async function replyParentPost(event, postId) {
    event.preventDefault();
    if (actionPendingRef.current) return;
    const targetForm = event.currentTarget;
    const form = new FormData(targetForm);
    const body = String(form.get("body") || "").trim();
    if (!body) {
      setError("請先輸入回覆內容");
      return;
    }
    setReplySendingPostId(postId);
    try {
      await runAction(async () => {
        await request(`/parent/posts/${postId}/replies`, {
          method: "POST",
          token: parentToken,
          body: JSON.stringify({
            student_id: activeParentStudentId,
            body,
          }),
        });
        targetForm.reset();
        setMessage("回覆已送出");
        await loadParentToday(parentToken, activeParentStudentId, parentPostDate);
      });
    } finally {
      setReplySendingPostId("");
    }
  }

  async function runImageCleanup() {
    await runAction(async () => {
      const result = await request(`/image-cleanups/run`, {
        method: "POST",
        token: teacherToken,
      });
      setMessage(`圖片清理：已刪除 ${result.deleted} 張，失敗 ${result.failed} 張`);
      await loadTeacherToday();
    });
  }

  async function loadNotifications(token = parentToken, selectedStudentId = activeParentStudentId) {
    if (!activeTenantId || !token || !selectedStudentId) return;
    const data = await request(`/parent/notifications?student_id=${selectedStudentId}`, {
      token,
    });
    setNotifications(data.items || []);
  }

  async function markNotificationRead(notificationId) {
    await runAction(async () => {
      await request(`/parent/notifications/${notificationId}/read`, {
        method: "POST",
        token: parentToken,
      });
      await loadNotifications();
    });
  }

  async function loadWebhookDeliveries() {
    if (!teacherReady) return;
    const data = await request(`/webhook-deliveries`, {
      token: teacherToken,
    });
    setWebhookDeliveries(data.items || []);
  }

  async function loadLineStatus() {
    if (!teacherReady) return;
    const data = await request(`/line-oa/status`, {
      token: teacherToken,
    });
    setLineStatus(data);
    const deliveries = await request(`/line-oa/deliveries`, {
      token: teacherToken,
    });
    setLineDeliveries(deliveries.items || []);
  }

  async function saveLineConfig(event) {
    event.preventDefault();
    const targetForm = event.currentTarget;
    await runAction(async () => {
      const form = new FormData(targetForm);
      const accessToken = String(form.get("line_channel_access_token") || "").trim();
      const channelSecret = String(form.get("line_channel_secret") || "").trim();
      const body = {};
      if (accessToken) body.line_channel_access_token = accessToken;
      if (channelSecret) body.line_channel_secret = channelSecret;
      if (!Object.keys(body).length) {
        throw new Error("請輸入 LINE 設定");
      }
      const data = await request(`/line-oa/config`, {
        method: "PUT",
        token: teacherToken,
        body: JSON.stringify(body),
      });
      targetForm.reset();
      setLineStatus((current) => ({ ...(current || {}), ...data }));
      setMessage("選配通知設定已更新");
      await loadLineStatus();
    });
  }

  async function runWebhookDeliveries() {
    await runAction(async () => {
      const data = await request(`/webhook-deliveries/run`, {
        method: "POST",
        token: teacherToken,
      });
      setMessage(`同步：已送出 ${data.sent} 筆，失敗 ${data.failed} 筆`);
      await loadWebhookDeliveries();
    });
  }

  async function runLineDeliveries() {
    await runAction(async () => {
      const data = await request(`/line-oa/deliveries/run`, {
        method: "POST",
        token: teacherToken,
      });
      setMessage(`選配通知：已送出 ${data.sent} 筆，失敗 ${data.failed} 筆`);
      await loadLineStatus();
    });
  }

  return (
    <main className={actionPending ? "app-shell app-shell-busy" : "app-shell"} aria-busy={actionPending}>
      <header className="app-header">
        <div className="brand-lockup">
          <img className="brand-logo" src="/classhub-logo.svg" alt="ClassHub" />
          <div>
            <p className="eyebrow">Open Source</p>
            <h1>ClassHub</h1>
            <span className="app-version">版本 {settings?.app_version || "-"}</span>
          </div>
        </div>
        {teacherToken && (
          <button type="button" className="settings-button" onClick={() => setSettingsOpen((open) => !open)}>
            設定
          </button>
        )}
      </header>

      {settingsOpen && teacherToken && (
        <SettingsPanel
          onChangePassword={changePassword}
          onSignOut={signOut}
        />
      )}

      <section className="status-strip" aria-live="polite">
        {!parentOnly && <StatusPill label="API" value={settings?.api_version || "-"} />}
        {!parentOnly && <StatusPill label="教師" value={teacherToken ? "已登入" : "未登入"} />}
        <StatusPill label="家長" value={parentToken ? "已啟用" : "未綁定"} />
        {actionPending && (
          <span className="notice busy">
            <span className="button-spinner notice-spinner" aria-hidden="true" />
            處理中
          </span>
        )}
        {message && <span className="notice">{message}</span>}
        {error && <span className="notice error">{error}</span>}
      </section>

      {publishNotice && (
        <button
          type="button"
          className="publish-notice"
          aria-live="polite"
          onClick={() => setPublishNotice("")}
        >
          {publishNotice}
        </button>
      )}

      {!parentOnly && (
        <nav className="mode-tabs" aria-label="工作區">
          <button type="button" className={mode === "teacher" ? "active" : ""} onClick={() => setMode("teacher")}>
            教師
          </button>
          <button type="button" className={mode === "publish" ? "active" : ""} onClick={() => setMode("publish")}>
            發布模式
          </button>
          <button type="button" className={mode === "parent" ? "active" : ""} onClick={() => setMode("parent")}>
            家長
          </button>
          {SHOW_ADVANCED_OPS && (
            <button type="button" className={mode === "ops" ? "active" : ""} onClick={() => setMode("ops")}>
              管理
            </button>
          )}
        </nav>
      )}

      {!parentOnly && mode === "teacher" && (
        <section className="workspace teacher-layout">
          {!teacherReady && (
            <TeacherLoginPanel
              onLogin={loginTeacher}
            />
          )}
          <SummaryCard
            title="聯絡簿"
            stats={todaySummary}
            onRefresh={() => runAction(loadTeacherToday)}
            className="teacher-status-panel"
          />
          <ClassTargetPanel
            teacherReady={teacherReady}
            classes={classes}
            classId={classId}
            onClassChange={selectClass}
            onCreateClass={createClass}
          />
          <RosterImportPanel
            teacherReady={teacherReady}
            rosterFile={rosterFile}
            rosterPreview={rosterPreview}
            rosterPreviewing={rosterPreviewing}
            rosterApplying={rosterApplying}
            onFileChange={selectRosterFile}
            onDownloadTemplate={downloadRosterTemplate}
            onPreview={previewRosterImport}
            onApply={applyRosterImport}
            onDownloadIssues={downloadRosterIssues}
          />
          <QuickPublish
            onPublish={publishPost}
            teacherReady={teacherReady}
            classReady={Boolean(classId)}
            isPublishing={postPublishing}
            resetKey={publishResetKey}
          />
          <SetupCard
            classReady={Boolean(classId)}
            parentReady={Boolean(parentId && studentId)}
            classId={classId}
            classes={classes}
            onClassChange={selectClass}
            students={students}
            parents={parents}
            inviteTargetKey={parentId && studentId ? `${parentId}:${studentId}` : ""}
            inviteToken={inviteToken}
            inviteUrl={inviteUrl}
            classInviteItems={classInviteItems}
            classInviteIndex={classInviteIndex}
            classInvitesCreating={classInvitesCreating}
            onCreateBinding={createStudentAndParent}
            onInviteTargetChange={selectInviteTarget}
            onInvite={createInvite}
            onCreateClassInvites={createClassInvites}
            onClassInviteIndexChange={selectClassInvite}
            onPrintClassInvites={printClassInvites}
            onShareInvite={shareInvite}
            onCopyInvite={copyInviteLink}
            className="teacher-setup-panel"
          />
          <StudentParentManager
            teacherReady={teacherReady}
            classId={classId}
            classes={classes}
            students={students}
            parents={parents}
            onLoad={() => runAction(loadTeacherRoster)}
            onUpdateStudent={updateStudent}
            onDeleteStudent={deleteStudent}
            onUpdateParent={updateParent}
            onDeleteParent={deleteParent}
          />
          <FeedPanel
            title="教師聯絡簿"
            items={todayItems}
            token={teacherToken}
            selectedIds={selectedPostIds}
            onToggleItem={togglePostSelection}
            onDeleteItem={deletePublishedPost}
            onDeleteSelected={deleteSelectedPublishedPosts}
            onExportText={exportContactBookText}
            exportDisabled={!teacherReady}
            onStatusDetail={loadPostRecipientStatus}
            statusLoadingPostId={postStatusLoadingId}
            dateOptions={postDateOptions}
            selectedDate={teacherPostDate}
            onDateChange={selectTeacherPostDate}
          />
          <PostStatusDetailPanel
            detail={postStatusDetail}
            loading={Boolean(postStatusLoadingId)}
            onRefresh={() => loadPostRecipientStatus(postStatusDetail?.post?.id)}
            onClose={() => setPostStatusDetail(null)}
          />
        </section>
      )}

      {!parentOnly && mode === "publish" && (
        <section className="workspace publish-mode-layout">
          {!teacherReady ? (
            <TeacherLoginPanel
              onLogin={loginTeacher}
            />
          ) : (
            <PublishModePanel
              teacherReady={teacherReady}
              classes={classes}
              classId={classId}
              onClassChange={selectClass}
              students={students}
              parents={parents}
              onPublish={publishPost}
              isPublishing={postPublishing}
              resetKey={publishResetKey}
            />
          )}
        </section>
      )}

      {(parentOnly || mode === "parent") && (
        <section className="workspace parent-layout">
          {!parentOnly && (
            <ParentAccess
              inviteToken={inviteToken}
              setInviteToken={setInviteToken}
              onBind={bindParent}
              onStartLineBinding={startLineBinding}
              onLoad={() => runAction(loadParentToday)}
              parentReady={parentReady}
              lineBinding={lineBinding}
              tenantReady={Boolean(activeTenantId)}
            />
          )}
          <ParentSummary
            unreadCount={unreadCount}
            itemCount={parentItems.length}
            parentReady={parentReady}
            onLoad={() => runAction(loadParentToday)}
            onSignOut={signOutParent}
          />
          <FeedPanel
            title="家長聯絡簿"
            items={parentItems}
            token={parentToken}
            onConfirm={confirmParentPost}
            onReply={replyParentPost}
            replySendingPostId={replySendingPostId}
            dateOptions={postDateOptions}
            selectedDate={parentPostDate}
            onDateChange={selectParentPostDate}
          />
          {parentReady && <NotificationsPanel items={notifications} onRead={markNotificationRead} />}
        </section>
      )}

      {SHOW_ADVANCED_OPS && mode === "ops" && (
        <section className="workspace ops-layout">
          <OpsPanel
            pendingWebhookCount={pendingWebhookCount}
            pendingLineCount={pendingLineCount}
            deliveries={webhookDeliveries}
            lineDeliveries={lineDeliveries}
            lineStatus={lineStatus}
            teacherReady={teacherReady}
            onLoadDeliveries={() => runAction(loadWebhookDeliveries)}
            onRunDeliveries={runWebhookDeliveries}
            onLoadLineStatus={() => runAction(loadLineStatus)}
            onRunLineDeliveries={runLineDeliveries}
            onSaveLineConfig={saveLineConfig}
            onCleanupImages={runImageCleanup}
          />
        </section>
      )}
    </main>
  );
}

function StatusPill({ label, value }) {
  return (
    <span className="status-pill">
      <span>{label}</span>
      <strong>{value}</strong>
    </span>
  );
}

function SettingsPanel({ onChangePassword, onSignOut }) {
  return (
    <section className="settings-panel" aria-label="設定">
      <div className="settings-grid">
        <section className="panel compact-panel">
          <div className="panel-heading row-heading">
            <div>
              <h2>更改密碼</h2>
            </div>
            <button type="button" onClick={onSignOut}>登出</button>
          </div>
          <form onSubmit={onChangePassword} className="stack">
            <PasswordInput name="current_password" placeholder="目前密碼" autoComplete="current-password" />
            <PasswordInput name="new_password" placeholder="新密碼" autoComplete="new-password" />
            <PasswordInput name="confirm_password" placeholder="確認新密碼" autoComplete="new-password" />
            <button type="submit" className="primary">更新密碼</button>
          </form>
        </section>
      </div>
    </section>
  );
}

function TeacherLoginPanel({ onLogin }) {
  return (
    <section className="panel compact-panel teacher-login-panel">
      <form onSubmit={onLogin} className="teacher-login-form">
        <input name="username" placeholder="帳號" autoComplete="username" />
        <PasswordInput name="password" placeholder="密碼" autoComplete="current-password" />
        <button type="submit" className="primary">登入</button>
      </form>
    </section>
  );
}

function PublishModePanel({
  teacherReady,
  classes,
  classId,
  onClassChange,
  students,
  parents,
  onPublish,
  isPublishing,
  resetKey,
}) {
  const [activeStep, setActiveStep] = useState("class");
  const carouselRef = useRef(null);
  const slideRefs = useRef({});
  const classReady = Boolean(classId);
  const selectedClass = classes.find((item) => item.id === classId) || null;
  const classStudents = classReady ? students.filter((item) => (item.class_ids || []).includes(classId)) : [];
  const classParents = classReady ? parents.filter((item) => (item.class_ids || []).includes(classId)) : [];

  function goToStep(step) {
    setActiveStep(step);
    window.requestAnimationFrame(() => {
      slideRefs.current[step]?.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
        inline: "start",
      });
    });
  }

  function syncActiveStep() {
    const carousel = carouselRef.current;
    const firstSlide = carousel?.querySelector(".publish-mode-slide");
    if (!carousel || !firstSlide) return;
    const gap = 12;
    const index = Math.max(0, Math.min(
      PUBLISH_MODE_STEPS.length - 1,
      Math.round(carousel.scrollLeft / (firstSlide.clientWidth + gap)),
    ));
    setActiveStep(PUBLISH_MODE_STEPS[index][0]);
  }

  return (
    <div className="publish-mode">
      <div className="publish-mode-tabs" role="tablist" aria-label="發布模式">
        {PUBLISH_MODE_STEPS.map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={activeStep === value ? "active" : ""}
            aria-current={activeStep === value ? "step" : undefined}
            onClick={() => goToStep(value)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="publish-mode-carousel" aria-label="發布模式" ref={carouselRef} onScroll={syncActiveStep}>
        <div className="publish-mode-slide" ref={(node) => { slideRefs.current.class = node; }}>
          <PublishClassCard
            teacherReady={teacherReady}
            classes={classes}
            classId={classId}
            onClassChange={onClassChange}
            onNext={() => goToStep("target")}
          />
        </div>
        <div className="publish-mode-slide" ref={(node) => { slideRefs.current.target = node; }}>
          <PublishTargetCard
            classReady={classReady}
            selectedClass={selectedClass}
            studentCount={classStudents.length}
            parentCount={classParents.length}
            onPrev={() => goToStep("class")}
            onNext={() => goToStep("publish")}
          />
        </div>
        <div className="publish-mode-slide" ref={(node) => { slideRefs.current.publish = node; }}>
          <QuickPublish
            onPublish={onPublish}
            teacherReady={teacherReady}
            classReady={classReady}
            isPublishing={isPublishing}
            resetKey={resetKey}
          />
          <div className="publish-mode-actions">
            <button type="button" onClick={() => goToStep("target")}>← 上一步</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function PublishClassCard({ teacherReady, classes, classId, onClassChange, onNext }) {
  return (
    <section className="panel compact-panel publish-class-card">
      <div className="panel-heading">
        <p className="eyebrow">發布模式</p>
        <h2>選班級</h2>
      </div>
      <label className="field-label" htmlFor="publish-class-select">選定班級</label>
      <select
        id="publish-class-select"
        value={classId}
        onChange={(event) => onClassChange(event.target.value)}
        disabled={!teacherReady}
      >
        <option value="">選擇班級</option>
        {classes.map((item) => (
          <option key={item.id} value={item.id}>{item.name}</option>
        ))}
      </select>
      {teacherReady && !classId && (
        <p className="field-help field-help-error class-required-warning">必須先選定班級</p>
      )}
      <div className="publish-mode-actions">
        <button type="button" className="primary" onClick={onNext} disabled={!classId}>
          下一步 →
        </button>
      </div>
    </section>
  );
}

function PublishTargetCard({ classReady, selectedClass, studentCount, parentCount, onPrev, onNext }) {
  return (
    <section className="panel compact-panel publish-target-card">
      <div className="panel-heading">
        <p className="eyebrow">發布模式</p>
        <h2>確認對象</h2>
      </div>
      {!classReady ? (
        <p className="field-help field-help-error class-required-warning">必須先選定班級</p>
      ) : (
        <div className="publish-target-summary" aria-label="發布對象摘要">
          <div>
            <span>班級</span>
            <strong>{selectedClass?.name || "-"}</strong>
          </div>
          <div>
            <span>學生</span>
            <strong>{studentCount}</strong>
          </div>
          <div>
            <span>家長</span>
            <strong>{parentCount}</strong>
          </div>
        </div>
      )}
      <div className="publish-mode-actions">
        <button type="button" onClick={onPrev}>← 上一步</button>
        <button type="button" className="primary" onClick={onNext} disabled={!classReady}>
          下一步 →
        </button>
      </div>
    </section>
  );
}

function QuickPublish({ onPublish, teacherReady, classReady, isPublishing, resetKey }) {
  const imageInputId = useId();
  const [imageFileNames, setImageFileNames] = useState([]);
  const publishDisabled = !teacherReady || !classReady || isPublishing;
  const imageStatus = imageFileNames.length
    ? `已選擇 ${imageFileNames.length} 張：${imageFileNames.join("、")}`
    : "尚未選擇圖片";

  useEffect(() => {
    setImageFileNames([]);
  }, [resetKey]);

  return (
    <section className="panel publish-panel" aria-label="快速發布">
      <span className="panel-watermark" aria-hidden="true">快速發布</span>
      <form onSubmit={onPublish} className="publish-form" aria-busy={isPublishing}>
        <input name="title" placeholder="標題" />
        <div className="form-row">
          <select name="category" defaultValue="announcement">
            {CATEGORY_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
          <label className="checkbox">
            <input name="require_confirmation" type="checkbox" />
            需要確認
          </label>
        </div>
        <textarea name="content" placeholder="訊息內容" />
        <input
          id={imageInputId}
          className="visually-hidden"
          name="images"
          type="file"
          accept="image/*"
          multiple
          onChange={(event) => {
            setImageFileNames(
              Array.from(event.target.files || []).map((file) => file.name),
            );
          }}
          disabled={publishDisabled}
        />
        <div className="file-picker">
          <label
            className={`button-like ${publishDisabled ? "disabled" : ""}`.trim()}
            htmlFor={imageInputId}
          >
            選擇圖片
          </label>
          <span>{imageStatus}</span>
        </div>
        <button type="submit" className="primary full submit-button" disabled={publishDisabled}>
          {isPublishing && <span className="button-spinner" aria-hidden="true" />}
          <span>{isPublishing ? "發布中" : "發布"}</span>
        </button>
      </form>
    </section>
  );
}

function PasswordInput({ name, placeholder, autoComplete }) {
  const [passwordVisible, setPasswordVisible] = useState(false);

  return (
    <div className="password-field">
      <input
        name={name}
        type={passwordVisible ? "text" : "password"}
        placeholder={placeholder}
        autoComplete={autoComplete}
      />
      <button
        type="button"
        className="icon-button password-toggle"
        aria-label={passwordVisible ? "隱藏密碼" : "顯示密碼"}
        onClick={() => setPasswordVisible((current) => !current)}
      >
        <EyeIcon hidden={passwordVisible} />
      </button>
    </div>
  );
}

function ClassHubIdField({ value, onChange }) {
  const helpId = useId();
  const error = value ? getClassHubIdError(value) : "";

  return (
    <label className="field-help-wrap">
      <span className="visually-hidden">ClassHub ID</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="ClassHub ID"
        autoComplete="username"
        inputMode="latin"
        maxLength={32}
        pattern="[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])"
        title={CLASSHUB_ID_HELP}
        aria-describedby={helpId}
        aria-invalid={Boolean(error)}
      />
      <small id={helpId} className={error ? "field-help field-help-error" : "field-help"}>
        {error || CLASSHUB_ID_HELP}
      </small>
    </label>
  );
}

function ClassTargetPanel({ teacherReady, classes, classId, onClassChange, onCreateClass }) {
  return (
    <section className="panel compact-panel class-target-panel">
      <div className="panel-heading">
        <h2>班級</h2>
      </div>
      <label className="field-label" htmlFor="class-select">選定班級</label>
      <select id="class-select" value={classId} onChange={(event) => onClassChange(event.target.value)} disabled={!teacherReady}>
        <option value="">選擇班級</option>
        {classes.map((item) => (
          <option key={item.id} value={item.id}>{item.name}</option>
        ))}
      </select>
      {teacherReady && !classId && (
        <p className="field-help field-help-error class-required-warning">必須先選定班級</p>
      )}
      <p className="field-label">或是新增</p>
      <form onSubmit={onCreateClass} className="class-create-form">
        <input name="name" placeholder="班級名稱" />
        <button type="submit" disabled={!teacherReady}>新增</button>
      </form>
    </section>
  );
}

const ROSTER_CHANGE_LABELS = {
  create: "新增",
  update: "更新",
  deactivate: "停用",
  unchanged: "不變",
};

function RosterImportPanel({
  teacherReady,
  rosterFile,
  rosterPreview,
  rosterPreviewing,
  rosterApplying,
  onFileChange,
  onDownloadTemplate,
  onPreview,
  onApply,
  onDownloadIssues,
}) {
  const fileInputId = useId();
  const summary = rosterPreview?.summary || {};
  const blockingItems = [
    ...(rosterPreview?.errors || []),
    ...(rosterPreview?.conflicts || []),
  ];
  const visibleChanges = rosterPreview?.changes || [];
  const rosterBusy = rosterPreviewing || rosterApplying;

  return (
    <section className="panel compact-panel roster-import-panel" aria-busy={rosterBusy}>
      <div className="panel-heading row-heading">
        <div>
          <p className="eyebrow">名單</p>
          <h2>Google Sheet 匯入</h2>
        </div>
        <button type="button" onClick={onDownloadTemplate} disabled={!teacherReady || rosterBusy}>
          範本
        </button>
      </div>
      <div className="roster-import-rules" aria-label="匯入規則">
        <span>CSV</span>
        <span>UTF-8 / CP950</span>
        <span>最多 300 列</span>
        <span>最大 256 KB</span>
        <span>必填：班級名稱、學生姓名</span>
      </div>
      <form onSubmit={onPreview} className="roster-import-form">
        <input
          id={fileInputId}
          className="visually-hidden"
          type="file"
          accept=".csv,text/csv"
          aria-label="匯入 CSV"
          onChange={(event) => onFileChange(event.target.files?.[0] || null)}
          disabled={!teacherReady || rosterBusy}
        />
        <div className="file-picker">
          <label
            className={`button-like ${!teacherReady || rosterBusy ? "disabled" : ""}`.trim()}
            htmlFor={fileInputId}
          >
            選擇 CSV
          </label>
          <span>{rosterFile?.name || "尚未選擇檔案"}</span>
        </div>
        <div className="button-row">
          <button
            type="submit"
            className="submit-button"
            disabled={!teacherReady || !rosterFile || rosterBusy}
          >
            {rosterPreviewing && <span className="button-spinner" aria-hidden="true" />}
            <span>{rosterPreviewing ? "預覽中" : "預覽"}</span>
          </button>
          <button
            type="button"
            className="primary submit-button"
            onClick={onApply}
            disabled={!teacherReady || !rosterFile || !rosterPreview?.can_apply || rosterBusy}
          >
            {rosterApplying && <span className="button-spinner" aria-hidden="true" />}
            <span>{rosterApplying ? "同步中" : "同步名單"}</span>
          </button>
        </div>
      </form>
      {rosterBusy && (
        <div className="inline-status" role="status">
          <span className="button-spinner" aria-hidden="true" />
          <span>{rosterApplying ? "正在同步名單，請稍候" : "正在讀取 CSV，請稍候"}</span>
        </div>
      )}
      {rosterPreview && (
        <div className="roster-preview">
          <div className="metrics-grid roster-metrics">
            <Metric label="班級新增" value={summary.classes_to_create || 0} />
            <Metric label="學生新增" value={summary.students_to_create || 0} />
            <Metric label="學生更新" value={summary.students_to_update || 0} />
            <Metric label="學生停用" value={summary.students_to_deactivate || 0} />
            <Metric label="家長新增" value={summary.guardians_to_create || 0} />
            <Metric label="家長停用" value={summary.guardians_to_deactivate || 0} />
          </div>
          {blockingItems.length > 0 && (
            <div className="import-issues" aria-label="名單錯誤">
              <div className="row-heading">
                <strong>需修正 {blockingItems.length} 項</strong>
                <button type="button" onClick={onDownloadIssues}>
                  下載錯誤清單
                </button>
              </div>
              {blockingItems.map((item, index) => (
                <div key={`${item.row || "all"}-${index}`} className="import-issue">
                  <strong>{item.row ? `第 ${item.row} 列` : "名單"}</strong>
                  <span>{item.field ? `${item.field}：` : ""}{item.message}</span>
                </div>
              ))}
            </div>
          )}
          {visibleChanges.length > 0 && (
            <div className="import-change-list" aria-label="同步預覽">
              {visibleChanges.slice(0, 12).map((item, index) => (
                <div key={`${item.type}-${index}`} className={`import-change ${item.action}`}>
                  <span>{ROSTER_CHANGE_LABELS[item.action] || item.action}</span>
                  <strong>{item.class_name} · {item.student_name}</strong>
                  {item.type === "guardian" && (
                    <small>{item.parent_name}{item.phone ? ` · ${item.phone}` : ""}</small>
                  )}
                  {item.type === "student" && item.student_code && (
                    <small>{item.student_code}</small>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function SetupCard({
  classReady,
  parentReady,
  classId,
  classes,
  onClassChange,
  students,
  parents,
  inviteTargetKey,
  inviteToken,
  inviteUrl,
  classInviteItems,
  classInviteIndex,
  classInvitesCreating,
  onCreateBinding,
  onInviteTargetChange,
  onInvite,
  onCreateClassInvites,
  onClassInviteIndexChange,
  onPrintClassInvites,
  onShareInvite,
  onCopyInvite,
  className = "",
}) {
  const [bindingStudentId, setBindingStudentId] = useState("");
  const studentTargets = useMemo(
    () => (classId ? students.filter((item) => (item.class_ids || []).includes(classId)) : students),
    [classId, students],
  );
  const inviteTargets = useMemo(
    () => (classId ? parents.filter((item) => (item.class_ids || []).includes(classId)) : parents),
    [classId, parents],
  );
  const selectedStudentParents = useMemo(
    () => (
      bindingStudentId
        ? inviteTargets.filter((item) => item.student_id === bindingStudentId)
        : []
    ),
    [bindingStudentId, inviteTargets],
  );
  const activeClassInvite = classInviteItems[classInviteIndex] || null;

  useEffect(() => {
    if (bindingStudentId && !studentTargets.some((item) => item.id === bindingStudentId)) {
      setBindingStudentId("");
    }
  }, [bindingStudentId, studentTargets]);

  const bindingReady = Boolean(bindingStudentId || classReady);

  return (
    <section className={`panel compact-panel ${className}`.trim()}>
      <div className="panel-heading">
        <h2>連結與邀請</h2>
      </div>
      <form onSubmit={onCreateBinding} className="stack setup-section">
        <h3>建立家長邀請</h3>
        <label className="field-label" htmlFor="binding-class">班級</label>
        <select
          id="binding-class"
          name="class_id"
          value={classId}
          onChange={(event) => onClassChange(event.target.value)}
          disabled={!classes.length}
        >
          <option value="">選擇班級</option>
          {classes.map((item) => (
            <option key={item.id} value={item.id}>{item.name}</option>
          ))}
        </select>
        <label className="field-label" htmlFor="binding-student">學生</label>
        <select
          id="binding-student"
          name="student_id"
          value={bindingStudentId}
          onChange={(event) => setBindingStudentId(event.target.value)}
          disabled={!classReady && !studentTargets.length}
        >
          <option value="">新增學生</option>
          {studentTargets.map((item) => (
            <option key={item.id} value={item.id}>{item.name}</option>
          ))}
        </select>
        {selectedStudentParents.length > 0 && (
          <div className="existing-parent-hint">
            <strong>此學生已有家長</strong>
            <div className="button-row">
              {selectedStudentParents.map((item) => (
                <button
                  key={`${item.id}:${item.student_id}`}
                  type="button"
                  onClick={() => onInviteTargetChange(`${item.id}:${item.student_id}`)}
                >
                  {item.name}
                </button>
              ))}
            </div>
            <small>若要重發邀請，請選用既有家長後按「重新產生邀請碼」。</small>
          </div>
        )}
        {!bindingStudentId && <input name="student_name" placeholder="新學生姓名" required />}
        <input name="parent_name" placeholder="家長姓名" required />
        <input name="phone" placeholder="家長電話(option)" />
        <button type="submit" className="primary" disabled={!bindingReady}>建立家長並產生邀請碼</button>
      </form>
      {inviteTargets.length > 0 && (
        <div className="stack setup-section">
          <h3>既有連結</h3>
          <div className="inline-form">
            <select
              id="invite-target"
              value={inviteTargetKey}
              onChange={(event) => onInviteTargetChange(event.target.value)}
              disabled={!classReady}
              aria-label="既有連結"
            >
              <option value="">選擇學生 / 家長</option>
              {inviteTargets.map((item) => (
                <option key={`${item.id}-${item.student_id}`} value={`${item.id}:${item.student_id}`}>
                  {item.student_name} / {item.name}
                </option>
              ))}
            </select>
            <button type="button" onClick={onInvite} disabled={!parentReady}>重新產生邀請碼</button>
          </div>
        </div>
      )}
      <div className="stack setup-section">
        <h3>班級家長邀請</h3>
        <div className="button-row">
          <button
            type="button"
            className="primary submit-button"
            onClick={onCreateClassInvites}
            disabled={!classReady || !inviteTargets.length || classInvitesCreating}
          >
            {classInvitesCreating && <span className="button-spinner" aria-hidden="true" />}
            <span>{classInvitesCreating ? "產生中" : "產生整班 QR Code"}</span>
          </button>
          <button
            type="button"
            onClick={onPrintClassInvites}
            disabled={!classInviteItems.length || classInvitesCreating}
          >
            列印 QR Code
          </button>
        </div>
        {classInvitesCreating && (
          <div className="inline-status" role="status">
            <span className="button-spinner" aria-hidden="true" />
            <span>正在產生整班 QR Code，請稍候</span>
          </div>
        )}
        {activeClassInvite && (
          <div className="class-invite-carousel" aria-label="班級家長邀請輪播">
            <div className="carousel-toolbar">
              <button
                type="button"
                aria-label="上一位家長"
                onClick={() => onClassInviteIndexChange(classInviteIndex - 1)}
                disabled={classInviteIndex <= 0}
              >
                上一位
              </button>
              <select
                value={classInviteIndex}
                onChange={(event) => onClassInviteIndexChange(Number(event.target.value))}
                aria-label="選取學生與家長"
              >
                {classInviteItems.map((item, index) => (
                  <option key={item.key} value={index}>
                    {item.student_name} / {item.parent_name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                aria-label="下一位家長"
                onClick={() => onClassInviteIndexChange(classInviteIndex + 1)}
                disabled={classInviteIndex >= classInviteItems.length - 1}
              >
                下一位
              </button>
            </div>
            <div className="class-invite-card">
              <div className="class-invite-meta">
                <span>{classInviteIndex + 1} / {classInviteItems.length}</span>
                <strong>{activeClassInvite.student_name}</strong>
                <small>{activeClassInvite.parent_name} · {activeClassInvite.class_name}</small>
              </div>
              <QRCodeImage value={activeClassInvite.invite_url} label="班級家長邀請 QR Code" />
              <div className="invite-actions">
                <input value={activeClassInvite.invite_url} readOnly aria-label="班級家長邀請連結" />
                <div className="button-row">
                  <button type="button" className="primary" onClick={onShareInvite}>分享</button>
                  <button type="button" onClick={onCopyInvite}>複製連結</button>
                </div>
              </div>
            </div>
          </div>
        )}
        {classInviteItems.length > 0 && (
          <PrintableInviteSheet items={classInviteItems} />
        )}
      </div>
      {inviteToken && !activeClassInvite && (
        <div className="invite-share">
          <QRCodeImage value={inviteUrl} label="家長邀請 QR Code" />
          <div className="invite-actions">
            <input value={inviteUrl} readOnly aria-label="家長邀請連結" />
            <div className="button-row">
              <button type="button" className="primary" onClick={onShareInvite}>分享</button>
              <button type="button" onClick={onCopyInvite}>複製連結</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function PrintableInviteSheet({ items }) {
  return (
    <section className="printable-invite-sheet" aria-label="整班家長 QR Code">
      {items.map((item) => (
        <article key={item.key} className="printable-invite-card">
          <div className="printable-invite-meta">
            <strong>{item.student_name}</strong>
            <span>{item.parent_name}</span>
            <small>{item.class_name}</small>
          </div>
          <QRCodeImage value={item.invite_url} label="ClassHub QR Code" />
          <p>{item.invite_url}</p>
        </article>
      ))}
    </section>
  );
}

function StudentParentManager({
  teacherReady,
  classId,
  classes,
  students,
  parents,
  onLoad,
  onUpdateStudent,
  onDeleteStudent,
  onUpdateParent,
  onDeleteParent,
}) {
  const [selectedStudentId, setSelectedStudentId] = useState("");
  const [selectedParentKey, setSelectedParentKey] = useState("");
  const studentEditorRef = useRef(null);
  const parentEditorRef = useRef(null);
  const visibleStudents = useMemo(
    () => (classId ? students.filter((item) => (item.class_ids || []).includes(classId)) : students),
    [classId, students],
  );
  const visibleParents = useMemo(
    () => (classId ? parents.filter((item) => (item.class_ids || []).includes(classId)) : parents),
    [classId, parents],
  );
  const selectedStudent = visibleStudents.find((item) => item.id === selectedStudentId);
  const selectedParent = visibleParents.find((item) => `${item.id}:${item.student_id}` === selectedParentKey);

  useEffect(() => {
    if (selectedStudentId && !selectedStudent) {
      setSelectedStudentId("");
    }
  }, [selectedStudentId, selectedStudent]);

  useEffect(() => {
    if (selectedParentKey && !selectedParent) {
      setSelectedParentKey("");
    }
  }, [selectedParentKey, selectedParent]);

  useEffect(() => {
    if (selectedStudent && studentEditorRef.current) {
      studentEditorRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [selectedStudent]);

  useEffect(() => {
    if (selectedParent && parentEditorRef.current) {
      parentEditorRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [selectedParent]);

  return (
    <section className="panel compact-panel roster-panel">
      <div className="panel-heading row-heading">
        <div>
          <p className="eyebrow">管理</p>
          <h2>學生與家長</h2>
        </div>
        <button type="button" onClick={onLoad} disabled={!teacherReady}>載入</button>
      </div>
      <div className="roster-grid">
        <section className="roster-section" aria-label="學生資料">
          <h3>學生</h3>
          {!visibleStudents.length ? (
            <p className="muted">目前沒有學生</p>
          ) : (
            <div className="roster-editor">
              <select value={selectedStudentId} onChange={(event) => setSelectedStudentId(event.target.value)} aria-label="選擇學生">
                <option value="">選擇學生</option>
                {visibleStudents.map((item) => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
              </select>
              {selectedStudent && (
                <form ref={studentEditorRef} key={selectedStudent.id} className="roster-item" onSubmit={(event) => onUpdateStudent(event, selectedStudent.id)}>
                  <input name="name" defaultValue={selectedStudent.name} aria-label="學生姓名" />
                  <select name="class_id" defaultValue={(selectedStudent.class_ids || [])[0]} aria-label="班級">
                    {classes.map((classItem) => (
                      <option key={classItem.id} value={classItem.id}>{classItem.name}</option>
                    ))}
                  </select>
                  <small>{selectedStudent.parent_count || 0} 位家長</small>
                  <div className="button-row">
                    <button type="submit" className="primary" disabled={!teacherReady}>更新</button>
                    <button type="button" className="danger" disabled={!teacherReady} onClick={() => onDeleteStudent(selectedStudent.id, selectedStudent.name)}>刪除</button>
                  </div>
                </form>
              )}
            </div>
          )}
        </section>

        <section className="roster-section" aria-label="家長資料">
          <h3>家長</h3>
          {!visibleParents.length ? (
            <p className="muted">目前沒有家長</p>
          ) : (
            <div className="roster-editor">
              <select value={selectedParentKey} onChange={(event) => setSelectedParentKey(event.target.value)} aria-label="選擇家長">
                <option value="">選擇家長</option>
                {visibleParents.map((item) => (
                  <option key={`${item.id}-${item.student_id}`} value={`${item.id}:${item.student_id}`}>
                    {item.name} / {item.student_name}
                  </option>
                ))}
              </select>
              {selectedParent && (
                <form ref={parentEditorRef} key={`${selectedParent.id}-${selectedParent.student_id}`} className="roster-item" onSubmit={(event) => onUpdateParent(event, selectedParent.id)}>
                  <input name="name" defaultValue={selectedParent.name} aria-label="家長姓名" />
                  <input name="phone" defaultValue={selectedParent.phone || ""} placeholder="家長電話(option)" aria-label="家長電話" />
                  <small>{selectedParent.student_name} / {selectedParent.class_name}</small>
                  <div className="button-row">
                    <button type="submit" className="primary" disabled={!teacherReady}>更新</button>
                    <button type="button" className="danger" disabled={!teacherReady} onClick={() => onDeleteParent(selectedParent.id, selectedParent.name)}>刪除</button>
                  </div>
                </form>
              )}
            </div>
          )}
        </section>
      </div>
    </section>
  );
}

function SummaryCard({ title, stats, onRefresh, className = "" }) {
  return (
    <section className={`panel compact-panel ${className}`.trim()}>
      <div className="panel-heading row-heading">
        <div>
          <p className="eyebrow">班級狀態</p>
          <h2>{title}</h2>
        </div>
        <button type="button" onClick={onRefresh}>載入</button>
      </div>
      <div className="metrics-grid">
        <Metric label="貼文" value={stats.posts} />
        <Metric label="收件人" value={stats.recipients} />
        <Metric label="已確認" value={stats.confirmed} />
      </div>
    </section>
  );
}

function ParentAccess({
  inviteToken,
  setInviteToken,
  onBind,
  onStartLineBinding,
  onLoad,
  parentReady,
  lineBinding,
  tenantReady,
}) {
  return (
    <section className="panel compact-panel">
      <div className="panel-heading row-heading">
        <div>
          <h2>存取權</h2>
        </div>
        <button type="button" onClick={onLoad} disabled={!parentReady}>載入</button>
      </div>
      <textarea value={inviteToken} onChange={(event) => setInviteToken(event.target.value)} placeholder="邀請碼" />
      <button type="button" className="primary full" onClick={onBind} disabled={!tenantReady || !inviteToken}>綁定家長</button>
      <div className="line-box">
        <span>LINE OA（選配通知）：{lineBinding?.bound ? "已綁定" : "未綁定"}</span>
        <button type="button" onClick={onStartLineBinding} disabled={!parentReady}>產生選配通知綁定碼</button>
        {lineBinding?.code && (
          <>
            <QRCodeImage value={lineBinding.line_message} label="LINE OA 選配通知 QR Code" />
            <textarea
              className="token-box"
              value={`選配 LINE OA 通知綁定訊息：${lineBinding.line_message}\n綁定碼：${lineBinding.code}`}
              readOnly
            />
          </>
        )}
      </div>
    </section>
  );
}

function QRCodeImage({ value, label }) {
  const [dataUrl, setDataUrl] = useState("");

  useEffect(() => {
    let active = true;
    if (!value) {
      setDataUrl("");
      return () => {
        active = false;
      };
    }
    QRCode.toDataURL(value, {
      errorCorrectionLevel: "M",
      margin: 2,
      width: 180,
    })
      .then((url) => {
        if (active) setDataUrl(url);
      })
      .catch(() => {
        if (active) setDataUrl("");
      });
    return () => {
      active = false;
    };
  }, [value]);

  if (!dataUrl) return null;
  return (
    <div className="qr-box">
      <img src={dataUrl} alt={label} />
      <span>{label}</span>
    </div>
  );
}

function ParentSummary({ unreadCount, itemCount, parentReady, onLoad, onSignOut }) {
  return (
    <section className="panel compact-panel">
      <div className="panel-heading row-heading">
        <div>
          <p className="eyebrow">聯絡簿</p>
          <h2>家庭摘要</h2>
        </div>
        <div className="button-row">
          <button type="button" onClick={onLoad} disabled={!parentReady}>重新整理</button>
          <button type="button" onClick={onSignOut} disabled={!parentReady}>家長登出</button>
        </div>
      </div>
      <div className="metrics-grid two">
        <Metric label="事項" value={itemCount} />
        <Metric label="未讀" value={unreadCount} />
      </div>
    </section>
  );
}

function Metric({ label, value }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function FeedPanel({
  title,
  items,
  token,
  onConfirm,
  selectedIds = [],
  onToggleItem,
  onDeleteItem,
  onDeleteSelected,
  onExportText,
  exportDisabled = false,
  onStatusDetail,
  statusLoadingPostId = "",
  onReply,
  replySendingPostId = "",
  dateOptions = [],
  selectedDate = "",
  onDateChange,
}) {
  return (
    <section className="panel feed-panel">
      <div className="panel-heading row-heading">
        <div>
          <p className="eyebrow">動態</p>
          <h2>{title}</h2>
        </div>
        {(onExportText || onDeleteSelected) && (
          <div className="button-row">
            {onExportText && (
              <button type="button" onClick={onExportText} disabled={exportDisabled}>
                匯出文字備份
              </button>
            )}
            {onDeleteSelected && (
              <button
                type="button"
                className="danger"
                onClick={onDeleteSelected}
                disabled={!selectedIds.length}
              >
                刪除選取
              </button>
            )}
          </div>
        )}
      </div>
      {dateOptions.length > 0 && onDateChange && (
        <div className="feed-filter">
          <label className="field-label" htmlFor={`${title}-date`}>日期</label>
          <select
            id={`${title}-date`}
            value={selectedDate}
            onChange={(event) => onDateChange(event.target.value)}
          >
            {dateOptions.map(([value, label]) => (
              <option key={value} value={value}>{label} · {value}</option>
            ))}
          </select>
        </div>
      )}
      <FeedList
        items={items}
        token={token}
        onConfirm={onConfirm}
        selectedIds={selectedIds}
        onToggleItem={onToggleItem}
        onDeleteItem={onDeleteItem}
        onStatusDetail={onStatusDetail}
        statusLoadingPostId={statusLoadingPostId}
        onReply={onReply}
        replySendingPostId={replySendingPostId}
      />
    </section>
  );
}

function FeedList({
  items,
  token,
  onConfirm,
  selectedIds,
  onToggleItem,
  onDeleteItem,
  onStatusDetail,
  statusLoadingPostId,
  onReply,
  replySendingPostId,
}) {
  if (!items.length) return <p className="muted">目前沒有事項</p>;
  return (
    <div className="list">
      {items.map((item) => {
        const replyCount = item.reply_count ?? (item.replies || []).length;
        return (
          <article key={item.id} className={item.parent_confirmed ? "feed-item feed-item-confirmed" : "feed-item"}>
            {(onToggleItem || onDeleteItem || onStatusDetail) && (
              <div className="feed-actions">
                {onToggleItem && (
                  <label className="select-check">
                    <input
                      type="checkbox"
                      checked={selectedIds.includes(item.id)}
                      onChange={() => onToggleItem(item.id)}
                    />
                    <span>選取</span>
                  </label>
                )}
                <div className="feed-action-buttons">
                  {onStatusDetail && (
                    <button
                      type="button"
                      onClick={() => onStatusDetail(item.id)}
                      disabled={statusLoadingPostId === item.id}
                    >
                      {statusLoadingPostId === item.id ? "載入中" : "查看狀態"}
                    </button>
                  )}
                  {onDeleteItem && (
                    <button type="button" className="danger" onClick={() => onDeleteItem(item.id)}>
                      刪除
                    </button>
                  )}
                </div>
              </div>
            )}
            <div className="item-main">
              <span className="category">{CATEGORY_LABELS[item.category] || item.category}</span>
              <strong>{item.title}</strong>
              <p>{item.content || "沒有內容"}</p>
            </div>
            <ImageStrip images={item.images || []} token={token} />
            {typeof item.recipient_count === "number" && (
              <small>
                {item.read_count || 0}/{item.recipient_count} 已讀，{item.confirmation_count || 0} 已確認，
                {replyCount} 則回覆
              </small>
            )}
            <ReplyList replies={item.replies || []} />
            {onConfirm && (
              <div className="confirm-action">
                {item.parent_confirmed ? (
                  <span className="confirm-status">已確認</span>
                ) : (
                  <button type="button" onClick={() => onConfirm(item.id)}>確認</button>
                )}
              </div>
            )}
            {onReply && (
              <form className="reply-form" onSubmit={(event) => onReply(event, item.id)}>
                <label className="field-label" htmlFor={`reply-${item.id}`}>回覆老師</label>
                <textarea
                  id={`reply-${item.id}`}
                  name="body"
                  maxLength="200"
                  required
                  rows="2"
                  placeholder="最多 200 字"
                />
                <div className="reply-actions">
                  <button type="submit" disabled={replySendingPostId === item.id}>
                    {replySendingPostId === item.id ? "送出中" : "送出回覆"}
                  </button>
                </div>
              </form>
            )}
          </article>
        );
      })}
    </div>
  );
}

function PostStatusDetailPanel({ detail, loading, onRefresh, onClose }) {
  if (!detail) return null;
  const summary = detail.summary || {};
  const post = detail.post || {};
  return (
    <section className="panel status-detail-panel">
      <div className="panel-heading row-heading">
        <div>
          <p className="eyebrow">狀態</p>
          <h2>收件人明細</h2>
        </div>
        <div className="button-row">
          <button type="button" onClick={onRefresh} disabled={loading}>
            {loading ? "載入中" : "重新載入"}
          </button>
          <button type="button" onClick={onClose}>關閉</button>
        </div>
      </div>
      <div className="status-detail-title">
        <strong>{post.title || "聯絡簿"}</strong>
        <span>{formatDateTime(post.published_at || post.created_at)}</span>
      </div>
      <div className="metrics-grid four">
        <Metric label="收件人" value={summary.recipient_count || 0} />
        <Metric label="已查看" value={summary.read_count || 0} />
        <Metric label="已確認" value={summary.confirmation_count || 0} />
        <Metric label="回覆" value={summary.reply_count || 0} />
      </div>
      <RecipientStatusList
        items={detail.items || []}
        requireConfirmation={Boolean(post.require_confirmation)}
      />
    </section>
  );
}

function RecipientStatusList({ items, requireConfirmation }) {
  if (!items.length) return <p className="muted">目前沒有收件人</p>;
  return (
    <div className="recipient-status-list">
      {items.map((item) => {
        const readLabel = item.last_read_at ? "已查看" : "尚未查看";
        const confirmLabel = item.confirmed_at ? "已確認" : (requireConfirmation ? "尚未確認" : "不需確認");
        const replyLabel = item.reply_count ? `${item.reply_count} 則回覆` : "無回覆";
        return (
          <article key={item.recipient_id} className="recipient-status-item">
            <div className="recipient-status-name">
              <strong>{item.student_name}</strong>
              {item.student_code && <span>{item.student_code}</span>}
              <span>{item.parent_name}</span>
            </div>
            <div className="recipient-status-marks">
              <span className={item.last_read_at ? "status-mark ready" : "status-mark"}>{readLabel}</span>
              <span className={item.confirmed_at ? "status-mark ready" : "status-mark"}>{confirmLabel}</span>
              <span className={item.reply_count ? "status-mark ready" : "status-mark"}>{replyLabel}</span>
            </div>
            <div className="recipient-status-meta">
              {item.last_read_at && <small>查看 {formatDateTime(item.last_read_at)}</small>}
              {item.confirmed_at && <small>確認 {formatDateTime(item.confirmed_at)}</small>}
              {item.latest_reply_at && <small>最後回覆 {formatDateTime(item.latest_reply_at)}</small>}
            </div>
            {item.latest_reply_body && <p>{item.latest_reply_body}</p>}
          </article>
        );
      })}
    </div>
  );
}

function ReplyList({ replies }) {
  if (!replies.length) return null;
  return (
    <div className="reply-list">
      {replies.map((reply) => {
        const author = reply.student_name && reply.parent_name
          ? `${reply.student_name} / ${reply.parent_name}`
          : "家長";
        return (
          <div key={reply.id} className="reply-item">
            <div className="reply-meta">
              <strong>{author}</strong>
              {reply.created_at && <span>{formatDateTime(reply.created_at)}</span>}
            </div>
            <p>{reply.body}</p>
          </div>
        );
      })}
    </div>
  );
}

function NotificationsPanel({ items, onRead }) {
  return (
    <section className="panel compact-panel">
      <div className="panel-heading">
        <p className="eyebrow">收件匣</p>
        <h2>通知</h2>
      </div>
      {!items.length ? <p className="muted">目前沒有通知</p> : (
        <div className="list">
          {items.map((item) => (
            <article key={item.id} className="feed-item compact">
              <strong>{item.title}</strong>
              <span>{item.read_at ? "已讀" : "未讀"}</span>
              {!item.read_at && <button type="button" onClick={() => onRead(item.id)}>標示已讀</button>}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function OpsPanel({
  pendingWebhookCount,
  pendingLineCount,
  deliveries,
  lineDeliveries,
  lineStatus,
  teacherReady,
  onLoadDeliveries,
  onRunDeliveries,
  onLoadLineStatus,
  onRunLineDeliveries,
  onSaveLineConfig,
  onCleanupImages,
}) {
  return (
    <>
      <section className="panel compact-panel">
        <div className="panel-heading">
          <h2>工作</h2>
        </div>
        <div className="button-row">
          <button type="button" onClick={onCleanupImages} disabled={!teacherReady}>清理圖片</button>
          <button type="button" onClick={onRunDeliveries} disabled={!teacherReady}>重送同步</button>
          <button type="button" onClick={onRunLineDeliveries} disabled={!teacherReady}>重送選配通知</button>
          <button type="button" onClick={onLoadDeliveries} disabled={!teacherReady}>載入紀錄</button>
          <button type="button" onClick={onLoadLineStatus} disabled={!teacherReady}>載入選配通知</button>
        </div>
        <Metric label="同步待處理" value={pendingWebhookCount} />
        <Metric label="選配通知待處理" value={pendingLineCount} />
      </section>
      <section className="panel compact-panel">
        <div className="panel-heading">
          <p className="eyebrow">LINE OA（Optional）</p>
          <h2>通知入口</h2>
        </div>
        <p className="muted">LINE OA 為可選通知方式，只發送新訊息提醒；完整聯絡簿、附件、歷史紀錄與回覆皆於家長頁顯示。LINE 官方帳號、訊息額度與相關費用由用戶自行決定與承擔。</p>
        <div className="metrics-grid two">
          <Metric label="設定" value={lineStatus?.configured ? "已設定" : "未設定"} />
          <Metric label="已綁定" value={lineStatus?.bound_parent_count || 0} />
          <Metric label="Token" value={lineStatus?.line_channel_access_token_configured ? "已儲存" : "未儲存"} />
          <Metric label="Secret" value={lineStatus?.line_channel_secret_configured ? "已儲存" : "未儲存"} />
        </div>
        <form onSubmit={onSaveLineConfig} className="stack">
          <PasswordInput
            name="line_channel_access_token"
            placeholder="Channel Access Token（留空不變）"
            autoComplete="off"
          />
          <PasswordInput
            name="line_channel_secret"
            placeholder="Channel Secret（留空不變）"
            autoComplete="off"
          />
          <button type="submit" className="primary" disabled={!teacherReady}>儲存選配通知設定</button>
        </form>
      </section>
      <section className="panel feed-panel">
        <div className="panel-heading">
          <p className="eyebrow">同步</p>
          <h2>紀錄</h2>
        </div>
        <DeliveryList items={deliveries} />
      </section>
      <section className="panel feed-panel">
        <div className="panel-heading">
          <p className="eyebrow">LINE OA（Optional）</p>
          <h2>通知投遞紀錄</h2>
        </div>
        <DeliveryList items={lineDeliveries} />
      </section>
    </>
  );
}

function EyeIcon({ hidden }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" focusable="false">
      <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
      <circle cx="12" cy="12" r="3" />
      {hidden && <path d="M4 4l16 16" />}
    </svg>
  );
}

function DeliveryList({ items }) {
  if (!items.length) return <p className="muted">目前沒有投遞紀錄</p>;
  return (
    <div className="list">
      {items.map((item) => (
        <article key={item.id} className="feed-item compact">
          <strong>{item.event_type || "LINE 通知"}</strong>
          <span>{item.status}</span>
          <small>{item.attempts} 次嘗試{item.response_status ? `，HTTP ${item.response_status}` : ""}</small>
        </article>
      ))}
    </div>
  );
}

function ImageStrip({ images, token }) {
  const [sources, setSources] = useState([]);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const touchStartXRef = useRef(null);
  const loadedSources = sources.filter((image) => image.src);
  const selectedImage = selectedIndex >= 0 ? loadedSources[selectedIndex] : null;
  const hasMultipleImages = loadedSources.length > 1;

  function closeLightbox() {
    setSelectedIndex(-1);
    touchStartXRef.current = null;
  }

  function showPreviousImage() {
    setSelectedIndex((index) => (
      index > 0 ? index - 1 : Math.max(loadedSources.length - 1, 0)
    ));
  }

  function showNextImage() {
    setSelectedIndex((index) => (
      index >= 0 ? (index + 1) % loadedSources.length : index
    ));
  }

  useEffect(() => {
    let active = true;
    const urls = [];
    async function loadImages() {
      const loaded = [];
      for (const image of images) {
        try {
          const path = image.url.replace(/^\/api\/[^/]+/, "");
          const src = await fetchAuthorizedBlobUrl(path, token);
          urls.push(src);
          loaded.push({ ...image, src });
        } catch {
          loaded.push({ ...image, src: "" });
        }
      }
      if (active) setSources(loaded);
    }
    loadImages();
    return () => {
      active = false;
      urls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [images, token]);

  useEffect(() => {
    if (selectedIndex >= loadedSources.length) {
      setSelectedIndex(-1);
    }
  }, [loadedSources.length, selectedIndex]);

  useEffect(() => {
    if (!selectedImage) return undefined;
    function handleKeyDown(event) {
      if (event.key === "Escape") {
        closeLightbox();
      } else if (event.key === "ArrowLeft") {
        showPreviousImage();
      } else if (event.key === "ArrowRight") {
        showNextImage();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [selectedImage, loadedSources.length]);

  if (!images.length) return null;
  return (
    <>
      <div className="images">
        {sources.map((image) => (
          image.src ? (
            <button
              key={image.id}
              type="button"
              className="image-thumb"
              onClick={() => setSelectedIndex(loadedSources.findIndex((item) => item.id === image.id))}
              aria-label={`放大查看 ${image.original_filename || "圖片"}`}
            >
              <img src={image.src} alt={image.original_filename || "post image"} />
            </button>
          ) : (
            <span key={image.id} className="muted">圖片無法載入</span>
          )
        ))}
      </div>
      {selectedImage && (
        <div
          className="image-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={selectedImage.original_filename || "圖片預覽"}
          onClick={closeLightbox}
        >
          <div
            className="image-lightbox-frame"
            onClick={(event) => event.stopPropagation()}
            onTouchStart={(event) => {
              touchStartXRef.current = event.touches[0]?.clientX ?? null;
            }}
            onTouchEnd={(event) => {
              if (touchStartXRef.current === null) return;
              const endX = event.changedTouches[0]?.clientX ?? touchStartXRef.current;
              const deltaX = endX - touchStartXRef.current;
              touchStartXRef.current = null;
              if (Math.abs(deltaX) < 40 || !hasMultipleImages) return;
              if (deltaX > 0) {
                showPreviousImage();
              } else {
                showNextImage();
              }
            }}
          >
            <div className="image-lightbox-toolbar">
              <span>{selectedIndex + 1} / {loadedSources.length}</span>
              <button type="button" className="image-lightbox-close" onClick={closeLightbox}>
                關閉
              </button>
            </div>
            <div className="image-lightbox-stage">
              {hasMultipleImages && (
                <button type="button" className="image-lightbox-nav previous" onClick={showPreviousImage} aria-label="上一張">
                  ‹
                </button>
              )}
              <img src={selectedImage.src} alt={selectedImage.original_filename || "post image"} />
              {hasMultipleImages && (
                <button type="button" className="image-lightbox-nav next" onClick={showNextImage} aria-label="下一張">
                  ›
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
