"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { apiFetch, wsBase } from "@/lib/api";
import { selectedModel } from "@/lib/models";

export interface ChatMessage {
  role: "user" | "argus";
  text: string;
  pending?: boolean;
}

interface ChatState {
  messages: ChatMessage[];
  busy: boolean;
  offline: boolean;
  send: (text: string) => void;
}

const ChatContext = createContext<ChatState | null>(null);

export function useChat(): ChatState {
  const state = useContext(ChatContext);
  if (!state) throw new Error("useChat must be used inside <ChatProvider>");
  return state;
}

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => () => socketRef.current?.close(), []);

  async function runPlanner(instruction: string) {
    setBusy(true);
    setMessages((prev) => [
      ...prev,
      { role: "user", text: `/plan ${instruction}` },
      { role: "argus", text: "", pending: true },
    ]);
    try {
      const response = await apiFetch("/api/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction: instruction || "Plan my day" }),
      });
      const payload = await response.json();
      const text = response.ok
        ? `Planned! ${payload.created} suggestion${payload.created === 1 ? "" : "s"} waiting on the Review page.`
        : `Planning failed: ${payload.detail}`;
      setMessages((prev) => [...prev.slice(0, -1), { role: "argus", text }]);
    } catch {
      setMessages((prev) => [
        ...prev.slice(0, -1),
        { role: "argus", text: "Planning failed — is the backend running?" },
      ]);
    }
    setBusy(false);
  }

  function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    if (message.startsWith("/plan")) {
      runPlanner(message.replace(/^\/plan\s*/, ""));
      return;
    }
    setBusy(true);
    setOffline(false);
    setMessages((prev) => [
      ...prev,
      { role: "user", text: message },
      { role: "argus", text: "", pending: true },
    ]);

    const ws = new WebSocket(`${wsBase()}/ws/chat`);
    socketRef.current = ws;
    // §7 model selection: send the chosen model with the frame. The backend
    // ws handler reads payload fields with .get() (backend/main.py ws_chat)
    // and ignores unknown keys, so this is forward-compatible — routing the
    // model is the backend branch's concern (flags.localModels: preview).
    ws.onopen = () => ws.send(JSON.stringify({ message, model: selectedModel() }));
    ws.onmessage = (event) => {
      const frame = JSON.parse(event.data);
      if (frame.type === "delta") {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, text: last.text + frame.text, pending: false };
          return next;
        });
      } else if (frame.type === "done") {
        setBusy(false);
        ws.close();
      } else if (frame.type === "error") {
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = {
            role: "argus",
            text: `Something went wrong: ${frame.detail}`,
            pending: false,
          };
          return next;
        });
        setBusy(false);
        ws.close();
      }
    };
    ws.onerror = () => {
      setOffline(true);
      setBusy(false);
      setMessages((prev) => prev.slice(0, -1));
    };
  }

  return (
    <ChatContext.Provider value={{ messages, busy, offline, send }}>
      {children}
    </ChatContext.Provider>
  );
}
