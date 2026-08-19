"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { apiFetch, wsBase } from "@/lib/api";
import { selectedModel } from "@/lib/models";

/** One dispatch of one tool, assembled from the `tool` start and end frames. */
export interface ToolStep {
  callId: string;
  name: string;
  args?: Record<string, unknown>;
  /** Present once the matching `phase:"end"` frame lands. */
  label?: string;
  detail?: string;
  paths?: string[];
  ok?: boolean;
  startedAt: number;
  endedAt?: number;
}

export type MessageStatus = "streaming" | "done" | "error" | "stopped";

export interface ChatMessage {
  /** Stable React key. Client-side only — see `serverId` for the DB row. */
  key: string;
  /** `chat_messages.id`, from the `done` frame. Absent until the turn ends. */
  serverId?: number;
  role: "user" | "assistant";
  text: string;
  steps: ToolStep[];
  status: MessageStatus;
  error?: string;
  startedAt?: number;
  endedAt?: number;
  /** `/plan` output: real, but never written to `chat_messages` (see runPlanner). */
  local?: boolean;
}

interface ChatState {
  messages: ChatMessage[];
  threadId: number | null;
  threadTitle: string;
  busy: boolean;
  offline: boolean;
  send: (text: string) => void;
  stop: () => void;
  newThread: () => void;
}

const ChatContext = createContext<ChatState | null>(null);

export function useChat(): ChatState {
  const state = useContext(ChatContext);
  if (!state) throw new Error("useChat must be used inside <ChatProvider>");
  return state;
}

let keySeq = 0;
const nextKey = () => `m${++keySeq}`;

/** Replace the last message in place. Every frame handler edits only the
 *  in-flight assistant turn, which is always the tail. */
function patchLast(
  list: ChatMessage[],
  patch: (message: ChatMessage) => ChatMessage,
): ChatMessage[] {
  if (list.length === 0) return list;
  const next = [...list];
  next[next.length - 1] = patch(next[next.length - 1]);
  return next;
}

/**
 * The chat client.
 *
 * One long-lived socket for the whole conversation, not one per message. The
 * old client opened a `WebSocket` per send and never sent `thread_id` back, so
 * the backend minted a fresh thread every time and history was never replayed
 * — the transcript on screen was decoration.
 *
 * `course` scopes the provider to one Course Hub: it rides on every outbound
 * frame, where `build_vault_tools` (backend/agent/runtime.py) turns it into a
 * forced `search_vault` filter rather than leaving the scope to the model.
 */
export function ChatProvider({ children, course }: { children: ReactNode; course?: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [threadId, setThreadId] = useState<number | null>(null);
  const [threadTitle, setThreadTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);

  const socketRef = useRef<WebSocket | null>(null);
  // Read inside send() and the frame handlers, which the socket captures once
  // at connect time — refs keep them from going stale without forcing a
  // reconnect on every render.
  const threadIdRef = useRef<number | null>(null);
  const busyRef = useRef(false);
  const stoppingRef = useRef(false);

  // Delta batching. The backend streams token by token, and one setState per
  // token means one full markdown re-parse per token over a growing string.
  // Deltas accumulate here and reach state once per animation frame.
  const bufferRef = useRef("");
  const frameRef = useRef<number | null>(null);

  const flush = useCallback(() => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    const chunk = bufferRef.current;
    if (!chunk) return;
    bufferRef.current = "";
    setMessages((prev) => patchLast(prev, (m) => ({ ...m, text: m.text + chunk })));
  }, []);

  const scheduleFlush = useCallback(() => {
    if (frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      flush();
    });
  }, [flush]);

  const settle = useCallback(
    (status: MessageStatus, patch: Partial<ChatMessage> = {}) => {
      flush();
      setMessages((prev) =>
        patchLast(prev, (m) =>
          m.role === "assistant" && m.status === "streaming"
            ? { ...m, status, endedAt: Date.now(), ...patch }
            : m,
        ),
      );
      setBusy(false);
      busyRef.current = false;
    },
    [flush],
  );

  const handleFrame = useCallback(
    (event: MessageEvent) => {
      let frame: Record<string, unknown>;
      try {
        frame = JSON.parse(event.data as string);
      } catch {
        return;
      }
      switch (frame.type) {
        case "thread": {
          const id = Number(frame.thread_id);
          if (Number.isFinite(id)) {
            threadIdRef.current = id;
            setThreadId(id);
          }
          setThreadTitle(String(frame.title ?? ""));
          break;
        }
        case "delta": {
          bufferRef.current += String(frame.text ?? "");
          scheduleFlush();
          break;
        }
        case "tool": {
          // Flush first, so the trace and the prose it interleaves with stay
          // in the order they were streamed.
          flush();
          const callId = String(frame.call_id ?? "");
          const name = String(frame.name ?? "");
          setMessages((prev) =>
            patchLast(prev, (m) => {
              const steps = [...m.steps];
              const at = steps.findIndex((step) => step.callId === callId);
              if (frame.phase === "start") {
                const step: ToolStep = {
                  callId,
                  name,
                  args: (frame.args as Record<string, unknown>) ?? undefined,
                  startedAt: Date.now(),
                };
                if (at === -1) steps.push(step);
                else steps[at] = { ...steps[at], ...step };
              } else if (at !== -1) {
                steps[at] = {
                  ...steps[at],
                  label: String(frame.label ?? name),
                  detail: String(frame.detail ?? ""),
                  paths: Array.isArray(frame.paths) ? (frame.paths as string[]) : [],
                  ok: frame.ok !== false,
                  endedAt: Date.now(),
                };
              }
              return { ...m, steps };
            }),
          );
          break;
        }
        case "done": {
          const id = Number(frame.message_id);
          settle("done", Number.isFinite(id) ? { serverId: id } : {});
          break;
        }
        case "error": {
          // Deliberately does NOT close the socket. ws_chat's `while True`
          // loop survives an application error — its own comment reads "agent
          // errors must reach the UI, not kill the socket" — so closing here
          // would throw away a working connection and force a pointless
          // reconnect. Only a transport-level close is a real disconnection.
          settle("error", { error: String(frame.detail ?? "unknown error") });
          break;
        }
        default:
          break;
      }
    },
    [flush, scheduleFlush, settle],
  );

  const connect = useCallback((): WebSocket => {
    const ws = new WebSocket(`${wsBase()}/ws/chat`);
    ws.onmessage = handleFrame;
    ws.onclose = () => {
      if (socketRef.current === ws) socketRef.current = null;
      if (stoppingRef.current) {
        stoppingRef.current = false;
        return;
      }
      // Closed with a turn still in flight and nobody asked for it: the
      // transport went away. The backend has already banked whatever partial
      // answer it had, so keep the text on screen and say what happened.
      if (busyRef.current) {
        setOffline(true);
        settle("error", { error: "connection lost" });
      }
    };
    return ws;
  }, [handleFrame, settle]);

  // The socket is opened lazily by send(), never by an effect — an
  // effect-owned socket double-opens under StrictMode's mount/cleanup/mount.
  // This cleanup therefore has nothing to close unless a turn actually ran.
  useEffect(
    () => () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      stoppingRef.current = true;
      socketRef.current?.close();
      socketRef.current = null;
    },
    [],
  );

  /**
   * `/plan` is a REST action, not a chat turn: it POSTs /api/plan and queues
   * suggestions on the Review page. No REST route appends a message to a
   * thread, so these two bubbles are `local` — they live in the transcript for
   * the session and are never written to `chat_messages`.
   */
  const runPlanner = useCallback(async (instruction: string) => {
    setBusy(true);
    busyRef.current = true;
    setMessages((prev) => [
      ...prev,
      {
        key: nextKey(),
        role: "user",
        text: `/plan ${instruction}`,
        steps: [],
        status: "done",
        local: true,
      },
      { key: nextKey(), role: "assistant", text: "", steps: [], status: "streaming", local: true },
    ]);
    let text: string;
    try {
      const response = await apiFetch("/api/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // `/plan` runs on the same model the chat header shows (§7) — the
        // selector would be misleading if planning quietly ignored it.
        body: JSON.stringify({ instruction: instruction || "Plan my day", model: selectedModel() }),
      });
      const payload = await response.json();
      text = response.ok
        ? `Planned! ${payload.created} suggestion${payload.created === 1 ? "" : "s"} waiting on the Review page.`
        : `Planning failed: ${payload.detail}`;
    } catch {
      text = "Planning failed — is the backend running?";
    }
    setMessages((prev) =>
      patchLast(prev, (m) => ({ ...m, text, status: "done", endedAt: Date.now() })),
    );
    setBusy(false);
    busyRef.current = false;
  }, []);

  const send = useCallback(
    (raw: string) => {
      const message = raw.trim();
      // Guarding on the ref rather than on `busy` also closes the double-click
      // window before the first `thread` frame lands, which would otherwise
      // mint two threads for one conversation.
      if (!message || busyRef.current) return;
      if (message.startsWith("/plan")) {
        void runPlanner(message.replace(/^\/plan\s*/, ""));
        return;
      }

      setOffline(false);
      setBusy(true);
      busyRef.current = true;
      setMessages((prev) => [
        ...prev,
        { key: nextKey(), role: "user", text: message, steps: [], status: "done" },
        {
          key: nextKey(),
          role: "assistant",
          text: "",
          steps: [],
          status: "streaming",
          startedAt: Date.now(),
        },
      ]);

      const payload = JSON.stringify({
        message,
        model: selectedModel(),
        ...(course ? { course } : {}),
        ...(threadIdRef.current !== null ? { thread_id: threadIdRef.current } : {}),
      });

      let ws = socketRef.current;
      // readyState 0 = CONNECTING, 1 = OPEN; anything above is closing or gone.
      if (!ws || ws.readyState > WebSocket.OPEN) {
        ws = connect();
        socketRef.current = ws;
      }
      if (ws.readyState === WebSocket.OPEN) ws.send(payload);
      else {
        const socket = ws;
        socket.addEventListener("open", () => socket.send(payload), { once: true });
      }
    },
    [connect, course, runPlanner],
  );

  /** Halt the turn by dropping the socket. The backend's `finally` banks the
   *  partial answer on disconnect, so nothing already said is lost. */
  const stop = useCallback(() => {
    if (!busyRef.current) return;
    stoppingRef.current = true;
    settle("stopped");
    socketRef.current?.close();
    socketRef.current = null;
  }, [settle]);

  const newThread = useCallback(() => {
    if (busyRef.current) stop();
    setMessages([]);
    setThreadTitle("");
    setThreadId(null);
    threadIdRef.current = null;
  }, [stop]);

  const value = useMemo(
    () => ({ messages, threadId, threadTitle, busy, offline, send, stop, newThread }),
    [messages, threadId, threadTitle, busy, offline, send, stop, newThread],
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}
