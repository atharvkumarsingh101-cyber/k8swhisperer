import React, { useState } from "react";

const EVENT_TYPES = [
  "ANOMALY_DETECTED",
  "DIAGNOSIS_COMPLETE",
  "PLAN_CREATED",
  "SAFETY_GATE",
  "AUTO_EXECUTE",
  "RESOLUTION_COMPLETE",
  "HITL_REQUESTED",
  "HITL_DECISION",
  "HITL_TIMEOUT",
  "HUMAN_ALERT",
  "HUMAN_RESOLUTION",
];

export default function LogForm({ onSubmit, submitting }) {
  const [eventType, setEventType] = useState("HITL_DECISION");
  const [pod,       setPod]       = useState("");
  const [detail,    setDetail]    = useState("");

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!pod.trim() || !detail.trim()) return;
    onSubmit({ eventType, pod: pod.trim(), detail: detail.trim() });
  };

  const inputClass =
    "w-full px-3 py-2 rounded-xl border border-slate-200 bg-slate-50 text-sm text-slate-800 " +
    "focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent transition";

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div>
          <label className="text-xs text-slate-500 mb-1 block">Event type</label>
          <select
            value={eventType}
            onChange={e => setEventType(e.target.value)}
            className={inputClass}
          >
            {EVENT_TYPES.map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs text-slate-500 mb-1 block">Pod name</label>
          <input
            type="text"
            placeholder="crash-loop-pod-abc123"
            value={pod}
            onChange={e => setPod(e.target.value)}
            className={inputClass}
          />
        </div>
        <div>
          <label className="text-xs text-slate-500 mb-1 block">Detail</label>
          <input
            type="text"
            placeholder="approved: delete pod"
            value={detail}
            onChange={e => setDetail(e.target.value)}
            className={inputClass}
          />
        </div>
      </div>
      <button
        onClick={handleSubmit}
        disabled={submitting || !pod.trim() || !detail.trim()}
        className="px-5 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed
                   text-white text-sm font-semibold rounded-xl transition-colors"
      >
        {submitting ? "Submitting to Stellar…" : "Log on-chain"}
      </button>
    </div>
  );
}
