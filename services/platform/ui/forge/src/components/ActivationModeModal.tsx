import { AlertTriangle, Container, Monitor } from "lucide-react";

interface Props {
    onLocal: () => void;
    onDocker: () => void;
    onClose: () => void;
    activatingMode: "local" | "docker" | null;
    error?: string;
}

export default function ActivationModeModal({ onLocal, onDocker, onClose, activatingMode, error }: Props) {
    const modeLabel = activatingMode === "local" ? "Local Process" : "Docker Container";
    const modeHint = activatingMode === "local"
        ? "Starting Flogo services as native processes…"
        : "Building images and launching Docker containers — this may take a moment…";

    return (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
            <div className="bg-zinc-900 rounded-xl shadow-2xl w-full max-w-md">
                <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-700">
                    <h3 className="font-semibold text-zinc-100">
                        {activatingMode && !error ? "Activating Agent…" : "Choose Activation Mode"}
                    </h3>
                    {(!activatingMode || error) && (
                        <button onClick={onClose} className="text-zinc-500 hover:text-zinc-200 text-xl leading-none">&times;</button>
                    )}
                </div>

                {activatingMode && !error ? (
                    <div className="p-8 flex flex-col items-center gap-5 text-center">
                        <div className="relative w-14 h-14">
                            <div className="absolute inset-0 rounded-full border-4 border-zinc-700 border-t-brand-500 animate-spin" />
                            <div className="absolute inset-0 flex items-center justify-center">
                                {activatingMode === "local"
                                    ? <Monitor size={18} className="text-brand-500" />
                                    : <Container size={18} className="text-brand-500" />}
                            </div>
                        </div>
                        <div>
                            <p className="font-semibold text-zinc-100">{modeLabel}</p>
                            <p className="text-xs text-zinc-400 mt-1 max-w-xs">{modeHint}</p>
                        </div>
                    </div>
                ) : error ? (
                    <div className="p-5 space-y-4">
                        <div className="flex items-start gap-3 bg-red-950 border border-red-900 rounded-xl p-4">
                            <AlertTriangle size={16} className="text-red-500 shrink-0 mt-0.5" />
                            <div>
                                <p className="text-sm font-medium text-red-400">Activation failed</p>
                                <p className="text-xs text-red-500 mt-1 font-mono break-all">{error}</p>
                            </div>
                        </div>
                        <div className="flex gap-2 justify-end">
                            <button
                                onClick={onClose}
                                className="px-4 py-2 text-sm font-medium text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded-lg transition-colors"
                            >
                                Close
                            </button>
                        </div>
                    </div>
                ) : (
                    <div className="p-5 space-y-3">
                        <p className="text-sm text-zinc-400 mb-1">How should the agent services be started?</p>
                        <button
                            onClick={onLocal}
                            className="flex items-start gap-4 w-full p-4 rounded-xl border-2 border-zinc-700 hover:border-brand-400 hover:bg-brand-500/10 transition-colors text-left"
                        >
                            <div className="shrink-0 mt-0.5 p-2 rounded-lg bg-zinc-800">
                                <Monitor size={20} className="text-zinc-400" />
                            </div>
                            <div>
                                <p className="font-semibold text-sm text-zinc-100">Local Process</p>
                                <p className="text-xs text-zinc-400 mt-0.5">
                                    Runs Flogo services directly on this machine as native processes. Fast startup, no Docker required.
                                </p>
                            </div>
                        </button>
                        <button
                            onClick={onDocker}
                            className="flex items-start gap-4 w-full p-4 rounded-xl border-2 border-zinc-700 hover:border-brand-400 hover:bg-brand-500/10 transition-colors text-left"
                        >
                            <div className="shrink-0 mt-0.5 p-2 rounded-lg bg-zinc-800">
                                <Container size={20} className="text-zinc-400" />
                            </div>
                            <div>
                                <p className="font-semibold text-sm text-zinc-100">Docker Container</p>
                                <p className="text-xs text-zinc-400 mt-0.5">
                                    Builds and launches Docker containers with Weaviate, Ollama, and all agent services. Isolated and production-ready.
                                </p>
                            </div>
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
