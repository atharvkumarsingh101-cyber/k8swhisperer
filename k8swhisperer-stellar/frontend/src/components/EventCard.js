import React, { useState } from "react";
import { explorerUrl } from "../stellar";
import { EVENT_COLORS } from "../App";

export default function EventCard({ event }) {
  const [expanded, setExpanded] = useState(false);
  const colorClass = EVENT_COLORS[event.event_type] || EVENT_COLORS.DEFAULT;
  const date = event.timestamp
    ? new Date(event.timestamp * 1000).toLocaleString()
    : "—";

  return (
    <div
      className="bg-white border border-slate-200 rounded-2xl p-4 hover:border-slate-300 transition-colors cursor-pointer"
      onClick={() => setExpanded(v => !v)}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <span className={`shrink-0 text-xs font-bold px-2.5 py-1 rounded-full border ${colorClass}`}>
            {event.event_type || "UNKNOWN"}
          </span>
          <code className="text-sm text-slate-600 truncate font-mono">
            {event.pod || "—"}
          </code>
        </div>
        <span className="shrink-0 text-xs text-slate-400">{date}</span>
      </div>

      {expanded && (
        <div className="mt-3 space-y-2">
          <p className="text-sm text-slate-700 bg-slate-50 rounded-xl px-4 py-3 leading-relaxed">
            {event.detail || "(no detail)"}
          </p>
          {event.tx_hash && (
            <a
              href={explorerUrl(event.tx_hash)}
              target="_blank"
              rel="noreferrer"
              onClick={e => e.stopPropagation()}
              className="inline-flex items-center gap-1.5 text-xs text-blue-600 hover:text-blue-800 font-mono"
            >
              <span className="w-2 h-2 rounded-full bg-blue-500 inline-block" />
              {event.tx_hash.slice(0, 20)}… — view on Stellar Expert
            </a>
          )}
        </div>
      )}
    </div>
  );
}
