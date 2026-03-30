import React from "react";

const Card = ({ label, value, sub, color }) => (
  <div className="bg-white border border-slate-200 rounded-2xl p-5">
    <p className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1">{label}</p>
    <p className={`text-3xl font-bold ${color}`}>{value}</p>
    {sub && <p className="text-xs text-slate-400 mt-1">{sub}</p>}
  </div>
);

export default function StatsBar({ stats, onChainCount }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
      <Card label="On-chain events" value={onChainCount} sub="confirmed on Stellar" color="text-blue-600" />
      <Card label="Total fetched"   value={stats.total}  sub="from contract"         color="text-slate-800" />
      <Card label="Auto-resolved"   value={stats.autoResolved} sub="no human needed" color="text-green-600" />
      <Card label="HITL decisions"  value={stats.hitl}   sub="approvals + rejections" color="text-amber-600" />
      <Card label="Human alerts"    value={stats.alerts} sub="alert_human triggered"  color="text-red-600" />
    </div>
  );
}
