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
    RefreshCw,
    Square,
    XCircle,
} from "lucide-react";
import {
    listPlatformServices,
    listRuntimeAgents,
    stopRuntimeAgent,
    startRuntimeAgent,
} from "../api";
import type { AgentRuntime, PlatformService } from "../types";

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
        ready: "bg-green-100 text-green-800",
        starting: "bg-yellow-100 text-yellow-800",
        degraded: "bg-red-100 text-red-800",
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

// ── Platform services panel ────────────────────────────────────────────────────

function ServicePanel({ title, icon, services }: { title: string; icon: ReactNode; services: PlatformService[] }) {
    return (
        <section>
            <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3 flex items-center gap-2">
                {icon} {title}
            </h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                {services.map((svc) => (
                    <div
                        key={svc.name}
                        className={`rounded-xl border px-4 py-3 flex items-start gap-3 bg-white shadow-sm ${svc.status === "online" ? "border-gray-200" : "border-red-200"
                            }`}
                    >
                        <StatusDot online={svc.status === "online"} />
                        <div className="min-w-0">
                            <p className="text-sm font-medium text-gray-800 truncate">{svc.name}</p>
                            <p className="text-xs text-gray-400">
                                :{svc.port}
                                {svc.pid ? <span className="ml-2 text-gray-400">PID {svc.pid}</span> : null}
                            </p>
                        </div>
                    </div>
                ))}
            </div>
        </section>
    );
}

// ── Agent runtime row ──────────────────────────────────────────────────────────

function AgentRow({
    agentId,
    runtime,
    onStop,
    stopping,
}: {
    agentId: string;
    runtime: AgentRuntime;
    onStop: () => void;
    stopping: boolean;
}) {
    const [expanded, setExpanded] = useState(false);
    const portOrder = ["chat", "sse_rest", "sse_events", "ingestion", "chainlit"] as const;

    return (
        <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
            {/* header row */}
            <div className="px-4 py-3 flex items-center gap-3">
                <button
                    onClick={() => setExpanded((p) => !p)}
                    className="text-gray-400 hover:text-gray-700 flex-shrink-0"
                    aria-label="toggle details"
                >
                    {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </button>

                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                        <p className="text-sm font-semibold text-gray-800 truncate">{runtime.agentName}</p>
                        <ReadinessBadge value={runtime.readiness} />
                        <span className="text-xs text-gray-400">slot {runtime.slot}</span>
                    </div>
                    <p className="text-xs text-gray-400 mt-0.5 font-mono truncate">{agentId}</p>
                </div>

                <div className="flex items-center gap-3 flex-shrink-0 text-xs text-gray-500">
                    <span>up {uptime(runtime.startedAt)}</span>
                    <button
                        onClick={onStop}
                        disabled={stopping}
                        className="flex items-center gap-1 px-2.5 py-1 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-50 transition-colors text-xs"
                        title="Force stop agent services"
                    >
                        <Square size={11} />
                        Stop
                    </button>
                </div>
            </div>

            {/* expanded detail */}
            {expanded && (
                <div className="border-t border-gray-100 px-4 py-3 bg-gray-50 grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
                    {/* ports + PIDs */}
                    <div>
                        <p className="font-semibold text-gray-600 mb-1.5">Services</p>
                        <table className="w-full">
                            <tbody>
                                {portOrder
                                    .filter((k) => runtime.ports[k])
                                    .map((k) => {
                                        const pid = runtime.pids?.[k];
                                        const health = runtime.health?.[k];
                                        return (
                                            <tr key={k} className="border-b border-gray-100 last:border-0">
                                                <td className="py-1 text-gray-500 w-24">{k}</td>
                                                <td className="py-1 font-mono text-gray-700">:{runtime.ports[k]}</td>
                                                <td className="py-1 text-gray-400">{pid ? `PID ${pid}` : "—"}</td>
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
                        <p className="font-semibold text-gray-600 mb-1.5">URLs</p>
                        {[
                            ["Chat API", runtime.chatApiUrl],
                            ["Chat UI", runtime.chatUiUrl],
                            ["SSE", runtime.sseUrl],
                            ["Ingestion", runtime.ingestionUrl],
                        ]
                            .filter(([, u]) => u)
                            .map(([label, url]) => (
                                <div key={label} className="flex gap-2 py-0.5">
                                    <span className="text-gray-400 w-16 flex-shrink-0">{label}</span>
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

    const stopMutation = useMutation({
        mutationFn: (agentId: string) => stopRuntimeAgent(agentId),
        onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-runtime-agents"] }),
    });

    const onlineSvcs = services.filter((s) => s.status === "online").length;
    const loading = loadingSvc || loadingAgents;

    return (
        <div className="min-h-screen bg-gray-50">
            {/* header */}
            <header className="bg-white border-b border-gray-200 px-6 py-4">
                <div className="max-w-5xl mx-auto flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <button
                            onClick={() => navigate("/")}
                            className="text-gray-400 hover:text-gray-700 p-1 rounded-lg hover:bg-gray-100 transition-colors"
                            aria-label="back to gallery"
                        >
                            <ArrowLeft size={18} />
                        </button>
                        <div className="flex items-center gap-2">
                            <Activity size={18} className="text-brand-500" />
                            <h1 className="text-lg font-semibold text-gray-800">Admin Console</h1>
                        </div>
                    </div>

                    <div className="flex items-center gap-3 text-sm text-gray-500">
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
                            className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-200 hover:border-gray-400 transition-colors"
                            title="Refresh"
                        >
                            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
                            Refresh
                        </button>
                    </div>
                </div>
            </header>

            <main className="max-w-5xl mx-auto px-6 py-8 space-y-10">
                {/* Platform services */}
                {loadingSvc ? (
                    <p className="text-sm text-gray-400">Loading platform services…</p>
                ) : (
                    <>
                        <ServicePanel
                            title="Platform Services"
                            icon={<Cpu size={14} />}
                            services={services.filter((s) => s.category === "platform")}
                        />
                        <ServicePanel
                            title="Agent Support Services"
                            icon={<Activity size={14} />}
                            services={services.filter((s) => s.category === "agent-support")}
                        />
                    </>
                )}

                {/* Running agent runtimes */}
                <section>
                    <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3 flex items-center gap-2">
                        <Activity size={14} /> Running Agent Services
                    </h2>

                    {loadingAgents ? (
                        <p className="text-sm text-gray-400">Loading agent runtimes…</p>
                    ) : runtimeAgents.length === 0 ? (
                        <div className="rounded-xl border border-dashed border-gray-200 bg-white px-6 py-10 text-center text-sm text-gray-400">
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
                                    stopping={stopMutation.isPending}
                                />
                            ))}
                        </div>
                    )}
                </section>
            </main>
        </div>
    );
}
