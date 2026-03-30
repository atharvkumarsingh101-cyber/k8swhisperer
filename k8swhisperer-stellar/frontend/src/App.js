import React, { useState, useEffect, useCallback } from "react";
import { getEvents, getEventCount, logEvent, explorerUrl, contractExplorerUrl } from "./stellar";
import EventCard from "./components/EventCard";
import StatsBar  from "./components/StatsBar";
import LogForm   from "./components/LogForm";

const POLL_INTERVAL = 15000; // 15 s auto-refresh

const EVENT_COLORS = {
  HITL_DECISION:       "bg-red-100 text-red-800 border-red-200",
  HITL_REQUESTED:      "bg-amber-100 text-amber-800 border-amber-200",
  AUTO_EXECUTE:        "bg-green-100 text-green-800 border-green-200",
  RESOLUTION_COMPLETE: "bg-emerald-100 text-emerald-800 border-emerald-200",
  ANOMALY_DETECTED:    "bg-blue-100 text-blue-800 border-blue-200",
  HUMAN_ALERT:         "bg-orange-100 text-orange-800 border-orange-200",
  HUMAN_RESOLUTION:    "bg-teal-100 text-teal-800 border-teal-200",
  SAFETY_GATE:         "bg-gray-100 text-gray-700 border-gray-200",
  DEFAULT:             "bg-purple-100 text-purple-800 border-purple-200",
};

export { EVENT_COLORS };

export default function App() {
  const [events,    setEvents]    = useState([]);
  const [count,     setCount]     = useState(0);
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState(null);
  const [lastSync,  setLastSync]  = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [txFeedback, setTxFeedback] = useState(null);

  const contractId = process.env.REACT_APP_CONTRACT_ID || "";

  const fetchData = useCallback(async () => {
    try {
      const [evts, cnt] = await Promise.all([getEvents(), getEventCount()]);
      // Newest first
      setEvents([...evts].reverse());
      setCount(cnt);
      setLastSync(new Date());
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [fetchData]);

  const handleLogEvent = async ({ eventType, pod, detail }) => {
    setSubmitting(true);
    setTxFeedback(null);
    try {
      const hash = await logEvent(eventType, pod, detail);
      setTxFeedback({ ok: true, hash });
      await fetchData();
    } catch (err) {
      setTxFeedback({ ok: false, message: err.message });
    } finally {
      setSubmitting(false);
    }
  };

  const stats = {
    total:       events.length,
    hitl:        events.filter(e => e.event_type === "HITL_DECISION").length,
    autoResolved:events.filter(e => e.event_type === "AUTO_EXECUTE").length,
    alerts:      events.filter(e => e.event_type === "HUMAN_ALERT").length,
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">

      {/* Nav */}
      <nav className="bg-white border-b border-slate-200 px-6 h-14 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-lg font-bold tracking-tight">
            K8s<span className="text-blue-600">Whisperer</span>
          </span>
          <span className="text-xs bg-blue-50 text-blue-700 border border-blue-200 px-2 py-0.5 rounded-full font-medium">
            Stellar Testnet
          </span>
        </div>
        <div className="flex items-center gap-4 text-xs text-slate-400">
          {lastSync && (
            <span>last sync {lastSync.toLocaleTimeString()}</span>
          )}
          {contractId && (
            <a
              href={contractExplorerUrl()}
              target="_blank"
              rel="noreferrer"
              className="text-blue-500 hover:text-blue-700 font-mono"
            >
              {contractId.slice(0, 8)}…{contractId.slice(-4)}
            </a>
          )}
          <button
            onClick={fetchData}
            className="px-3 py-1 bg-slate-100 hover:bg-slate-200 rounded-lg transition-colors"
          >
            refresh
          </button>
        </div>
      </nav>

      <main className="max-w-5xl mx-auto px-4 py-8 space-y-6">

        {/* Stats */}
        <StatsBar stats={stats} onChainCount={count} />

        {/* Manual log form */}
        <div className="bg-white border border-slate-200 rounded-2xl p-6">
          <h2 className="text-sm font-semibold text-slate-700 mb-4 uppercase tracking-wide">
            Log event on-chain
          </h2>
          <LogForm onSubmit={handleLogEvent} submitting={submitting} />
          {txFeedback && (
            <div className={`mt-3 p-3 rounded-xl text-sm ${
              txFeedback.ok
                ? "bg-green-50 text-green-800 border border-green-200"
                : "bg-red-50 text-red-800 border border-red-200"
            }`}>
              {txFeedback.ok ? (
                <>
                  Transaction confirmed!{" "}
                  <a
                    href={explorerUrl(txFeedback.hash)}
                    target="_blank"
                    rel="noreferrer"
                    className="underline font-mono"
                  >
                    {txFeedback.hash.slice(0, 16)}…
                  </a>
                </>
              ) : (
                `Error: ${txFeedback.message}`
              )}
            </div>
          )}
        </div>

        {/* Event feed */}
        <div>
          <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-3">
            On-chain audit log — newest first
          </h2>

          {loading && (
            <div className="text-center py-16 text-slate-400">
              Fetching events from Stellar…
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-2xl p-6 text-red-700 text-sm">
              Could not reach the contract: {error}
            </div>
          )}

          {!loading && !error && events.length === 0 && (
            <div className="text-center py-16 text-slate-400">
              No events logged yet. Use the form above or run the K8sWhisperer pipeline.
            </div>
          )}

          <div className="space-y-3">
            {events.map((evt, i) => (
              <EventCard key={i} event={evt} />
            ))}
          </div>
        </div>

      </main>

      <footer className="text-center py-8 text-xs text-slate-400">
        K8sWhisperer · Soroban AuditLog · Stellar Testnet ·{" "}
        {contractId && (
          <a href={contractExplorerUrl()} target="_blank" rel="noreferrer" className="underline">
            view contract
          </a>
        )}
      </footer>
    </div>
  );
}
