import { useState, type ReactNode } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
    Activity,
    ArrowLeft,
    CheckCircle,
    ChevronDown,
    ChevronRight,
    CircleDot,
    Cpu,
    FileText,
    Play,
    RefreshCw,
    RotateCcw,
    Square,
    XCircle,
} from "lucide-react";
import {
    listAgents,
    listPlatformServices,
    listRuntimeAgents,
    stopRuntimeAgent,
    startRuntimeAgent,
    restartRuntimeAgent,
    stopPlatformService,
    startPlatformService,
    restartPlatformService,
    getAgentLogs,
    getPlatformLogs,
} from "../api";
import type { Agent, AgentRuntime, PlatformService } from "../types";
import ThemeToggle from "../components/ThemeToggle";

// ── Helpers ────────────────────────────────────────────────────────────────────

function uptime(startedAt: number): string {
    const secs = Math.floor(Date.now() / 1000 - startedAt);
    if (secs < 60) return `${secs}s`;
    if (secs < 3600) return `${Math.floor(secs / 60)}m ${secs % 60}s`;
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    return `${h}h ${m}m`;
}

function ReadinessBadge({ value }: { value: AgentRuntime["readiness"] }) {
    const map: Record<string, string> = {
        ready: "bg-green-950 text-green-400",
        starting: "bg-yellow-950 text-yellow-400",
        degraded: "bg-red-950 text-red-400",
    };
    return (
        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${map[value] ?? map.degraded}`}>
            <CircleDot size={10} />
            {value}
        </span>
    );
}

function StatusDot({ online }: { online: boolean }) {
    return online
        ? <CheckCircle size={16} className="text-green-500 flex-shrink-0" />
        : <XCircle size={16} className="text-red-400 flex-shrink-0" />;
}

// ── Shared log line renderer ───────────────────────────────────────────────────

function LogLines({ lines }: { lines: string[] }) {
    return (
        <pre className="bg-gray-900 rounded-lg p-3 overflow-auto max-h-72 text-[11px] font-mono leading-relaxed whitespace-pre-wrap">
            {lines.map((line, i) => {
                const isError = /error|failed|exception|panic/i.test(line);
                const isWarn = /\bwarn\b/i.test(line);
                return (
                    <span key={i} className={`block ${isError ? "text-red-400" : isWarn ? "text-yellow-300" : "text-gray-100"}`}>
                        {line}
                    </span>
                );
            })}
        </pre>
    );
}

// ── Agent log viewer (inside expanded AgentRow) ───────────────────────────────

const AGENT_LOG_SVCS = ["chat", "ingestion", "rule-engine", "chainlit"] as const;

function AgentLogViewer({ agentId }: { agentId: string }) {
    const [activeSvc, setActiveSvc] = useState<string>("chat");
    const [liveMode, setLiveMode] = useState(true);
    const [lineCount, setLineCount] = useState(80);

    const { data, isLoading, refetch, isFetching } = useQuery({
        queryKey: ["agent-logs", agentId, activeSvc, lineCount],
        queryFn: () => getAgentLogs(agentId, activeSvc, lineCount),
        refetchInterval: liveMode ? 3000 : false,
    });

    const lines = data?.lines ?? [];

    return (
        <div className="border-t border-zinc-700 pt-3">
            {/* toolbar */}
            <div className="flex items-center gap-1.5 mb-2 flex-wrap">
                <FileText size={11} className="text-zinc-500" />
                <span className="text-xs font-semibold text-zinc-500 mr-1">Logs</span>
                {AGENT_LOG_SVCS.map((svc) => (
                    <button
                        key={svc}
                        onClick={() => setActiveSvc(svc)}
                        className={`px-2 py-0.5 rounded text-xs font-mono transition-colors ${activeSvc === svc ? "bg-zinc-700 text-white" : "bg-zinc-800 text-zinc-500 hover:bg-zinc-700"
                            }`}
                    >
                        {svc}
                    </button>
                ))}
                <div className="ml-auto flex items-center gap-1.5">
                    <button
                        onClick={() => setLiveMode((v) => !v)}
                        className={`px-2 py-0.5 rounded border text-xs transition-colors ${liveMode
                            ? "border-green-700 text-green-400 bg-green-950/50"
                            : "border-zinc-700 text-zinc-500 hover:border-zinc-500"
                            }`}
                    >
                        {liveMode ? "● live" : "paused"}
                    </button>
                    <button
                        onClick={() => refetch()}
                        className="p-0.5 rounded border border-zinc-700 hover:border-zinc-500 transition-colors"
                        title="Refresh"
                    >
                        <RefreshCw size={10} className={isFetching ? "animate-spin text-zinc-400" : "text-zinc-600"} />
                    </button>
                    <select
                        value={lineCount}
                        onChange={(e) => setLineCount(Number(e.target.value))}
                        className="text-xs border border-zinc-700 rounded px-1 py-0.5 bg-zinc-800 text-zinc-300"
                    >
                        <option value={50}>50 lines</option>
                        <option value={80}>80 lines</option>
                        <option value={150}>150 lines</option>
                        <option value={300}>300 lines</option>
                    </select>
                </div>
            </div>

            {isLoading ? (
                <div className="bg-zinc-900 rounded-lg p-3 text-xs text-zinc-500 font-mono">Loading…</div>
            ) : !data?.exists ? (
                <div className="bg-zinc-900 rounded-lg p-3 text-xs text-zinc-600 font-mono">
                    No log file yet — service may not have started.
                </div>
            ) : lines.length === 0 ? (
                <div className="bg-zinc-900 rounded-lg p-3 text-xs text-zinc-600 font-mono">Log is empty.</div>
            ) : (
                <LogLines lines={lines} />
            )}
        </div>
    );
}

// ── Platform log viewer (inline below service card grid) ──────────────────────

function PlatformLogViewer({ service, onClose }: { service: string; onClose: () => void }) {
    const [liveMode, setLiveMode] = useState(true);
    const [lineCount, setLineCount] = useState(80);

    const { data, isLoading, refetch, isFetching } = useQuery({
        queryKey: ["platform-logs", service, lineCount],
        queryFn: () => getPlatformLogs(service, lineCount),
        refetchInterval: liveMode ? 4000 : false,
    });

    const lines = data?.lines ?? [];

    return (
        <div className="mt-3 p-3 bg-zinc-900 rounded-xl border border-zinc-800">
            {/* toolbar */}
            <div className="flex items-center gap-2 mb-2 flex-wrap">
                <FileText size={12} className="text-zinc-500" />
                <span className="text-xs font-semibold text-zinc-300">{service}</span>
                <div className="ml-auto flex items-center gap-1.5">
                    <button
                        onClick={() => setLiveMode((v) => !v)}
                        className={`px-2 py-0.5 rounded border text-xs transition-colors ${liveMode
                            ? "border-green-700 text-green-400 bg-green-950/50"
                            : "border-zinc-700 text-zinc-500 hover:border-zinc-500"
                            }`}
                    >
                        {liveMode ? "● live" : "paused"}
                    </button>
                    <button
                        onClick={() => refetch()}
                        className="p-0.5 rounded border border-zinc-700 hover:border-zinc-500 transition-colors"
                        title="Refresh"
                    >
                        <RefreshCw size={10} className={isFetching ? "animate-spin text-zinc-400" : "text-zinc-600"} />
                    </button>
                    <select
                        value={lineCount}
                        onChange={(e) => setLineCount(Number(e.target.value))}
                        className="text-xs border border-zinc-700 rounded px-1 py-0.5 bg-zinc-800 text-zinc-300"
                    >
                        <option value={50}>50 lines</option>
                        <option value={80}>80 lines</option>
                        <option value={150}>150 lines</option>
                    </select>
                    <button onClick={onClose} className="p-0.5 rounded hover:bg-zinc-800 transition-colors" title="Close">
                        <XCircle size={14} className="text-zinc-500 hover:text-zinc-300" />
                    </button>
                </div>
            </div>

            {isLoading ? (
                <div className="bg-zinc-900 rounded-lg p-3 text-xs text-zinc-500 font-mono">Loading…</div>
            ) : !data?.exists ? (
                <div className="bg-zinc-900 rounded-lg p-3 text-xs text-zinc-600 font-mono">No log file found.</div>
            ) : lines.length === 0 ? (
                <div className="bg-zinc-900 rounded-lg p-3 text-xs text-zinc-600 font-mono">Log is empty.</div>
            ) : (
                <LogLines lines={lines} />
            )}
        </div>
    );
}

// ── Platform services panel ────────────────────────────────────────────────────

function ServicePanel({
    title, icon, services,
    onStop, onStart, onRestart,
    pendingName, pendingAction,
}: {
    title: string;
    icon: ReactNode;
    services: PlatformService[];
    onStop: (name: string) => void;
    onStart: (name: string) => void;
    onRestart: (name: string) => void;
    pendingName?: string;
    pendingAction?: "stop" | "start" | "restart" | null;
}) {
    const [selectedLog, setSelectedLog] = useState<string | null>(null);
    return (
        <section>
            <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3 flex items-center gap-2">
                {icon} {title}
            </h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                {services.map((svc) => {
                    const isPending = pendingName === svc.name;
                    const isOnline = svc.status === "online";
                    return (
                        <div
                            key={svc.name}
                            onClick={() => setSelectedLog(selectedLog === svc.name ? null : svc.name)}
                            className={[
                                "rounded-xl border px-4 py-3 bg-zinc-900 cursor-pointer hover:border-zinc-500 transition-colors",
                                isOnline ? "border-zinc-700" : "border-red-900",
                                selectedLog === svc.name ? "ring-1 ring-blue-400" : "",
                            ].join(" ")}
                            title="Click to view logs"
                        >
                            {/* top row: status + name + log icon */}
                            <div className="flex items-start gap-2">
                                <StatusDot online={isOnline} />
                                <div className="min-w-0 flex-1">
                                    <p className="text-sm font-medium text-zinc-200 truncate">{svc.name}</p>
                                    <p className="text-xs text-zinc-500">
                                        :{svc.port}
                                        {svc.pid ? <span className="ml-2">PID {svc.pid}</span> : null}
                                    </p>
                                </div>
                                <FileText size={12} className={`flex-shrink-0 mt-0.5 ${selectedLog === svc.name ? "text-blue-400" : "text-zinc-700"}`} />
                            </div>

                            {/* action buttons */}
                            {svc.controllable && (
                                <div
                                    className="flex items-center gap-1 mt-2.5 pt-2 border-t border-zinc-700"
                                    onClick={(e) => e.stopPropagation()}
                                >
                                    {isOnline ? (
                                        <>
                                            <button
                                                onClick={() => onRestart(svc.name)}
                                                disabled={isPending}
                                                title="Restart service"
                                                className="flex items-center gap-1 px-2 py-0.5 rounded border border-zinc-700 text-zinc-400 hover:border-zinc-500 hover:text-zinc-200 disabled:opacity-50 transition-colors text-xs"
                                            >
                                                <RotateCcw size={10} className={isPending && pendingAction === "restart" ? "animate-spin" : ""} />
                                                {isPending && pendingAction === "restart" ? "Restarting…" : "Restart"}
                                            </button>
                                            <button
                                                onClick={() => onStop(svc.name)}
                                                disabled={isPending}
                                                title="Stop service"
                                                className="flex items-center gap-1 px-2 py-0.5 rounded border border-red-900 text-red-400 hover:bg-red-950 disabled:opacity-50 transition-colors text-xs"
                                            >
                                                <Square size={10} />
                                                {isPending && pendingAction === "stop" ? "Stopping…" : "Stop"}
                                            </button>
                                        </>
                                    ) : (
                                        <button
                                            onClick={() => onStart(svc.name)}
                                            disabled={isPending}
                                            title="Start service"
                                            className="flex items-center gap-1 px-2 py-0.5 rounded border border-green-800 text-green-400 hover:bg-green-950 disabled:opacity-50 transition-colors text-xs"
                                        >
                                            <Play size={10} />
                                            {isPending && pendingAction === "start" ? "Starting…" : "Start"}
                                        </button>
                                    )}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
            {selectedLog && (
                <PlatformLogViewer service={selectedLog} onClose={() => setSelectedLog(null)} />
            )}
        </section>
    );
}

// ── Agent runtime row ──────────────────────────────────────────────────────────

function AgentRow({
    agentId,
    runtime,
    onStop,
    stopping,
    onRestart,
    restarting,
}: {
    agentId: string;
    runtime: AgentRuntime;
    onStop: () => void;
    stopping: boolean;
    onRestart: () => void;
    restarting: boolean;
}) {
    const [expanded, setExpanded] = useState(false);
    const portOrder = ["chat", "sse_rest", "sse_events", "ingestion", "chainlit"] as const;

    return (
        <div className="rounded-xl border border-zinc-700 bg-zinc-900 overflow-hidden">
            {/* header row */}
            <div className="px-4 py-3 flex items-center gap-3">
                <button
                    onClick={() => setExpanded((p) => !p)}
                    className="text-zinc-500 hover:text-zinc-200 flex-shrink-0"
                    aria-label="toggle details"
                >
                    {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </button>

                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                        <p className="text-sm font-semibold text-zinc-200 truncate">{runtime.agentName}</p>
                        <ReadinessBadge value={runtime.readiness} />
                        <span className="text-xs text-zinc-500">slot {runtime.slot}</span>
                    </div>
                    <p className="text-xs text-zinc-600 mt-0.5 font-mono truncate">{agentId}</p>
                </div>

                <div className="flex items-center gap-2 flex-shrink-0 text-xs text-zinc-500">
                    <span>up {uptime(runtime.startedAt)}</span>
                    <button
                        onClick={onRestart}
                        disabled={restarting || stopping}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-lg border border-zinc-700 text-zinc-400 hover:bg-zinc-800 disabled:opacity-50 transition-colors text-xs"
                        title="Restart all agent services"
                    >
                        <RotateCcw size={11} className={restarting ? "animate-spin" : ""} />
                        {restarting ? "Restarting…" : "Restart"}
                    </button>
                    <button
                        onClick={onStop}
                        disabled={stopping || restarting}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-lg border border-red-900 text-red-400 hover:bg-red-950 disabled:opacity-50 transition-colors text-xs"
                        title="Force stop agent services"
                    >
                        <Square size={11} />
                        {stopping ? "Stopping…" : "Stop"}
                    </button>
                </div>
            </div>

            {/* expanded detail */}
            {expanded && (
                <div className="border-t border-zinc-800 px-4 py-3 bg-zinc-950/40 space-y-4 text-xs">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        {/* ports + PIDs */}
                        <div>
                            <p className="font-semibold text-zinc-400 mb-1.5">Services</p>
                            <table className="w-full">
                                <tbody>
                                    {portOrder
                                        .filter((k) => runtime.ports[k])
                                        .map((k) => {
                                            const pid = runtime.pids?.[k];
                                            const health = runtime.health?.[k];
                                            return (
                                                <tr key={k} className="border-b border-zinc-800 last:border-0">
                                                    <td className="py-1 text-zinc-500 w-24">{k}</td>
                                                    <td className="py-1 font-mono text-zinc-300">:{runtime.ports[k]}</td>
                                                    <td className="py-1 text-zinc-600">{pid ? `PID ${pid}` : "—"}</td>
                                                    <td className="py-1">
                                                        {health === "running"
                                                            ? <span className="text-green-600">●</span>
                                                            : health === "dead"
                                                                ? <span className="text-red-400">●</span>
                                                                : null}
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                </tbody>
                            </table>
                        </div>

                        {/* URLs */}
                        <div>
                            <p className="font-semibold text-zinc-400 mb-1.5">URLs</p>
                            {[
                                ["Chat API", runtime.chatApiUrl],
                                ["Chat UI", runtime.chatUiUrl],
                                ["SSE", runtime.sseUrl],
                                ["Ingestion", runtime.ingestionUrl],
                            ]
                                .filter(([, u]) => u)
                                .map(([label, url]) => (
                                    <div key={label} className="flex gap-2 py-0.5">
                                        <span className="text-zinc-500 w-16 flex-shrink-0">{label}</span>
                                        <a
                                            href={url}
                                            target="_blank"
                                            rel="noreferrer"
                                            className="font-mono text-brand-600 hover:underline truncate"
                                        >
                                            {url}
                                        </a>
                                    </div>
                                ))}
                        </div>
                    </div>
                    <AgentLogViewer agentId={agentId} />
                </div>
            )}
        </div>
    );
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function Admin() {
    const navigate = useNavigate();
    const qc = useQueryClient();

    const { data: services = [], isLoading: loadingSvc } = useQuery({
        queryKey: ["admin-services"],
        queryFn: listPlatformServices,
        refetchInterval: 10_000,
    });

    const { data: runtimeAgents = [], isLoading: loadingAgents } = useQuery({
        queryKey: ["admin-runtime-agents"],
        queryFn: listRuntimeAgents,
        refetchInterval: 5_000,
    });

    const { data: allAgents = [] } = useQuery({
        queryKey: ["all-agents"],
        queryFn: () => listAgents(),
        refetchInterval: 15_000,
    });

    const stopSvcMutation = useMutation({
        mutationFn: (name: string) => stopPlatformService(name),
        onSettled: () => qc.invalidateQueries({ queryKey: ["admin-services"] }),
    });

    const startSvcMutation = useMutation({
        mutationFn: (name: string) => startPlatformService(name),
        onSettled: () => qc.invalidateQueries({ queryKey: ["admin-services"] }),
    });

    const restartSvcMutation = useMutation({
        mutationFn: (name: string) => restartPlatformService(name),
        onSettled: () => qc.invalidateQueries({ queryKey: ["admin-services"] }),
    });

    const stopMutation = useMutation({
        mutationFn: (agentId: string) => stopRuntimeAgent(agentId),
        onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-runtime-agents"] }),
    });

    const restartMutation = useMutation({
        mutationFn: (agentId: string) => restartRuntimeAgent(agentId),
        onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-runtime-agents"] }),
    });

    const startMutation = useMutation({
        mutationFn: (agentId: string) => startRuntimeAgent(agentId),
        onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-runtime-agents"] }),
    });

    const runningIds = new Set(runtimeAgents.map((r) => r.agentId));
    const inactiveAgents = allAgents.filter((a) => !runningIds.has(a.id) && a.status !== "archived");

    const onlineSvcs = services.filter((s) => s.status === "online").length;
    const loading = loadingSvc || loadingAgents;

    return (
        <div className="min-h-screen bg-zinc-950">
            {/* header */}
            <header className="bg-zinc-900 border-b border-zinc-800 px-6 py-4">
                <div className="max-w-5xl mx-auto flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <button
                            onClick={() => navigate("/")}
                            className="text-zinc-500 hover:text-zinc-200 p-1 rounded-lg hover:bg-zinc-800 transition-colors"
                            aria-label="back to gallery"
                        >
                            <ArrowLeft size={18} />
                        </button>
                        <div className="flex items-center gap-2">
                            <Activity size={18} className="text-brand-500" />
                            <h1 className="text-lg font-semibold text-zinc-100">Admin Console</h1>
                        </div>
                    </div>

                    <div className="flex items-center gap-3 text-sm text-zinc-500">
                        {!loading && (
                            <span>
                                {onlineSvcs}/{services.length} platform&nbsp;·&nbsp;
                                {runtimeAgents.length} agent{runtimeAgents.length !== 1 ? "s" : ""} running
                            </span>
                        )}
                        <button
                            onClick={() => {
                                qc.invalidateQueries({ queryKey: ["admin-services"] });
                                qc.invalidateQueries({ queryKey: ["admin-runtime-agents"] });
                            }}
                            className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-zinc-700 hover:border-zinc-500 transition-colors text-zinc-400 hover:text-zinc-200"
                            title="Refresh"
                        >
                            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
                            Refresh
                        </button>
                        <ThemeToggle />
                    </div>
                </div>
            </header>

            <main className="max-w-5xl mx-auto px-6 py-8 space-y-10">
                {/* Platform services */}
                {loadingSvc ? (
                    <p className="text-sm text-zinc-500">Loading platform services…</p>
                ) : (
                    <ServicePanel
                        title="Platform Services"
                        icon={<Cpu size={14} />}
                        services={services.filter((s) => s.category === "platform")}
                        onStop={(n) => stopSvcMutation.mutate(n)}
                        onStart={(n) => startSvcMutation.mutate(n)}
                        onRestart={(n) => restartSvcMutation.mutate(n)}
                        pendingName={stopSvcMutation.variables ?? startSvcMutation.variables ?? restartSvcMutation.variables}
                        pendingAction={restartSvcMutation.isPending ? "restart" : stopSvcMutation.isPending ? "stop" : startSvcMutation.isPending ? "start" : null}
                    />
                )}

                {/* Running agent runtimes */}
                <section>
                    <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3 flex items-center gap-2">
                        <Activity size={14} /> Running Agent Services
                    </h2>

                    {loadingAgents ? (
                        <p className="text-sm text-zinc-500">Loading agent runtimes…</p>
                    ) : runtimeAgents.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-zinc-700 bg-zinc-900 px-6 py-10 text-center text-sm text-zinc-500">
                            No agent services are currently running.
                            <br />
                            Deploy an agent from the{" "}
                            <button
                                onClick={() => navigate("/")}
                                className="text-brand-500 hover:underline"
                            >
                                gallery
                            </button>{" "}
                            to see it here.
                        </div>
                    ) : (
                        <div className="space-y-3">
                            {runtimeAgents.map((rt) => (
                                <AgentRow
                                    key={rt.agentId}
                                    agentId={rt.agentId}
                                    runtime={rt}
                                    onStop={() => stopMutation.mutate(rt.agentId)}
                                    stopping={stopMutation.isPending && stopMutation.variables === rt.agentId}
                                    onRestart={() => restartMutation.mutate(rt.agentId)}
                                    restarting={restartMutation.isPending && restartMutation.variables === rt.agentId}
                                />
                            ))}
                        </div>
                    )}
                </section>

                {/* Inactive agents — can be started from here */}
                {inactiveAgents.length > 0 && (
                    <section>
                        <h2 className="text-sm font-semibold text-zinc-500 uppercase tracking-wider mb-3 flex items-center gap-2">
                            <Square size={14} /> Inactive Agents
                        </h2>
                        <div className="space-y-2">
                            {inactiveAgents.map((agent) => (
                                <div
                                    key={agent.id}
                                    className="rounded-xl border border-zinc-700 bg-zinc-900 px-4 py-3 flex items-center gap-3"
                                >
                                    <div className="w-7 h-7 rounded-lg bg-zinc-800 flex items-center justify-center text-xs font-bold text-zinc-400 flex-shrink-0">
                                        {agent.name.charAt(0).toUpperCase()}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <p className="text-sm font-medium text-zinc-200 truncate">{agent.name}</p>
                                        <p className="text-xs text-zinc-600 font-mono truncate">{agent.id}</p>
                                    </div>
                                    <span className="text-xs text-zinc-500 px-2 py-0.5 rounded-full bg-zinc-800">
                                        {agent.status}
                                    </span>
                                    <button
                                        onClick={() => startMutation.mutate(agent.id)}
                                        disabled={startMutation.isPending && startMutation.variables === agent.id}
                                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-green-800 text-green-400 hover:bg-green-950 disabled:opacity-50 transition-colors text-xs font-medium"
                                        title="Start agent services"
                                    >
                                        <Play size={11} />
                                        {startMutation.isPending && startMutation.variables === agent.id ? "Starting…" : "Start"}
                                    </button>
                                </div>
                            ))}
                        </div>
                    </section>
                )}
            </main>
        </div>
    );
}
