import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ExternalLink, Loader2, MoreHorizontal, Play, Square } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { cloneAgent, deleteAgent, purgeAgent } from "../api";
import type { Agent } from "../types";
import ActivationModeModal from "./ActivationModeModal";


// Deterministic avatar colour per agent name
function agentAvatarColor(name: string): string {
  const palette = [
    "bg-violet-500", "bg-brand-500", "bg-emerald-500",
    "bg-amber-500", "bg-rose-500", "bg-cyan-600",
    "bg-indigo-500", "bg-orange-500",
  ];
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) & 0xffffff;
  return palette[h % palette.length];
}

const STATUS_STYLES: Record<string, string> = {
  active: "bg-green-950 text-green-400",
  starting: "bg-blue-950 text-blue-400",
  activating: "bg-blue-950 text-blue-400",
  deactivating: "bg-amber-950 text-amber-400",
  draft: "bg-zinc-700 text-zinc-300",
  archived: "bg-red-950 text-red-400",
};

const STATUS_BORDER: Record<string, string> = {
  active: "border-l-green-400",
  activating: "border-l-blue-400",
  deactivating: "border-l-amber-400",
  draft: "border-l-slate-300",
  archived: "border-l-red-300",
};

interface AgentCardProps {
  agent: Agent;
  onToggleDeploy?: () => void;
  /** Callback when user picks Local Process mode from the activation modal */
  onActivateLocal?: () => void;
  /** Callback when user picks Docker Container mode from the activation modal */
  onActivateDocker?: () => void;
  deployPending?: boolean;
  /** "activating" | "deactivating" — explicit intent so labels/colours are correct before status flips */
  deployIntent?: "activating" | "deactivating";
  /** True while deployment.py is still spinning up this agent's services. */
  isStarting?: boolean;
}

export default function AgentCard({ agent, onToggleDeploy, onActivateLocal, onActivateDocker, deployPending, deployIntent, isStarting }: AgentCardProps) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const [activateModeOpen, setActivateModeOpen] = useState(false);
  const [activatingMode, setActivatingMode] = useState<"local" | "docker" | null>(null);
  const [confirmDeactivate, setConfirmDeactivate] = useState(false);

  // Auto-close activation modal once deployment is confirmed (deployPending goes false)
  const prevPendingRef = useRef(deployPending ?? false);
  useEffect(() => {
    if (prevPendingRef.current && !deployPending && activatingMode) {
      setActivateModeOpen(false);
      setActivatingMode(null);
    }
    prevPendingRef.current = deployPending ?? false;
  }, [deployPending, activatingMode]);

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  const removeMutation = useMutation({
    mutationFn: () => deleteAgent(agent.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  });

  const purgeMutation = useMutation({
    mutationFn: () => purgeAgent(agent.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  });

  const cloneMutation = useMutation({
    mutationFn: () => cloneAgent(agent.id),
    onSuccess: (cloned) => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      navigate(`/agents/${cloned.id}`);
    },
  });

  const provider = agent.config?.llmProvider ?? "—";
  const model = agent.config?.llmModel ?? "default model";
  const isActive = agent.status === "active";
  const avatarColor = agentAvatarColor(agent.name);
  const avatarInitial = agent.name.trim()[0]?.toUpperCase() ?? "A";

  // Optimistic display state while the toggle is in-flight.
  // deployIntent is the explicit intent passed from the parent — avoids relying
  // on agent.status which hasn't flipped yet when the request just fired.
  const pendingKey = deployPending ? (deployIntent ?? (isActive ? "deactivating" : "activating")) : null;
  const displayStatus = pendingKey ?? (isStarting ? "starting" : agent.status);
  const borderClass = STATUS_BORDER[pendingKey ?? agent.status] ?? STATUS_BORDER.draft;

  // Button should reflect the intent, not the current status
  const buttonIsDeactivating = deployPending ? deployIntent === "deactivating" : isActive;

  // chatUrl is only set once deployment.py has patched chatUiUrl into design-service
  // (which now happens only after all services are confirmed ready).
  const chatUrl = isActive ? (agent.config?.chatUiUrl || null) : null;

  return (
    <div
      className={`bg-zinc-800 rounded-xl border border-zinc-600 border-l-4 ${borderClass} flex flex-col hover:border-zinc-500 transition-all duration-300 cursor-pointer ${deployPending ? "ring-2 ring-blue-800 ring-offset-zinc-950 ring-offset-1" :
        displayStatus === "active" || displayStatus === "starting" ? "ring-1 ring-green-700 shadow-lg shadow-green-950/40" : ""
        }`}
      onClick={() => navigate(`/agents/${agent.id}`)}
    >
      {/* Card body */}
      <div className="p-5 flex flex-col gap-2 flex-1">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 flex-1 min-w-0">
            {/* Avatar */}
            <div className={`shrink-0 w-8 h-8 rounded-lg ${avatarColor} flex items-center justify-center text-white text-sm font-bold select-none`}>
              {avatarInitial}
            </div>
            <h3 className="font-semibold text-zinc-100 truncate">{agent.name}</h3>
            {(displayStatus === "active" || displayStatus === "starting") && (
              <span className="shrink-0 w-2 h-2 rounded-full bg-green-400 animate-pulse" title="Live" />
            )}
          </div>

          {/* Status badge + overflow menu — stop propagation so click doesn't open editor */}
          <div className="flex items-center gap-1.5 shrink-0" onClick={(e) => e.stopPropagation()}>
            <span className={`text-xs font-medium px-2 py-0.5 rounded-full transition-colors duration-300 ${STATUS_STYLES[displayStatus] ?? STATUS_STYLES.draft}`}>
              {displayStatus}
            </span>

            <div className="relative" ref={menuRef}>
              <button
                onClick={(e) => { e.stopPropagation(); setMenuOpen((o) => !o); }}
                className="text-zinc-400 hover:text-zinc-100 p-0.5 rounded hover:bg-zinc-700 transition-colors"
                title="More options"
              >
                <MoreHorizontal size={15} />
              </button>

              {menuOpen && (
                <div className="absolute right-0 top-full mt-1 bg-zinc-700 border border-zinc-600 rounded-lg shadow-xl shadow-zinc-950 z-10 min-w-[128px] py-1">
                  <button
                    onClick={(e) => { e.stopPropagation(); setMenuOpen(false); navigate(`/agents/${agent.id}`); }}
                    className="w-full text-left text-sm px-3 py-1.5 hover:bg-zinc-600 text-zinc-100"
                  >
                    Edit
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setMenuOpen(false); cloneMutation.mutate(); }}
                    disabled={cloneMutation.isPending}
                    className="w-full text-left text-sm px-3 py-1.5 hover:bg-zinc-600 text-zinc-200 disabled:opacity-40"
                  >
                    {cloneMutation.isPending ? "Cloning…" : "Clone"}
                  </button>
                  <div className="border-t border-zinc-600 my-1" />
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setMenuOpen(false);
                      if (confirm(`Archive agent "${agent.name}"?`)) removeMutation.mutate();
                    }}
                    disabled={removeMutation.isPending || agent.status === "archived"}
                    className="w-full text-left text-sm px-3 py-1.5 hover:bg-red-950 text-red-400 disabled:opacity-40"
                  >
                    Archive
                  </button>
                  {agent.status === "archived" && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setMenuOpen(false);
                        if (confirm(`Permanently delete "${agent.name}"? This cannot be undone.`)) purgeMutation.mutate();
                      }}
                      disabled={purgeMutation.isPending}
                      className="w-full text-left text-sm px-3 py-1.5 hover:bg-red-900 text-red-400 font-medium disabled:opacity-40"
                    >
                      {purgeMutation.isPending ? "Deleting…" : "Delete Permanently"}
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>

        {agent.description && (
          <p className="text-sm text-zinc-300 line-clamp-2">{agent.description}</p>
        )}

        <div className="mt-auto pt-1 space-y-0.5">
          <div className="text-xs text-zinc-400">{provider} · {model}</div>
          {agent.config?.collectionName && (
            <div className="text-xs text-zinc-400 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-sm bg-zinc-400" />
              <span className="font-mono">{agent.config.collectionName}</span>
            </div>
          )}
          <div className="text-xs text-zinc-500">v{agent.version} · {new Date(agent.updated_at).toLocaleDateString()}</div>
        </div>
      </div>

      {/* Footer actions — stop propagation so clicks don't open editor */}
      <div
        className="border-t border-zinc-700 px-4 py-2.5 flex items-center gap-2"
        onClick={(e) => e.stopPropagation()}
      >
        {onToggleDeploy && (
          <button
            onClick={() => {
              if (buttonIsDeactivating) {
                setConfirmDeactivate(true);
              } else if (onActivateLocal || onActivateDocker) {
                setActivateModeOpen(true);
              } else {
                onToggleDeploy();
              }
            }}
            disabled={deployPending || isStarting}
            className={`flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full transition-colors disabled:opacity-60 ${buttonIsDeactivating
              ? "bg-amber-950/50 text-amber-400 hover:bg-amber-900"
              : "bg-green-950/50 text-green-400 hover:bg-green-900"
              }`}
          >
            {deployPending ? (
              <>
                <Loader2 size={11} className="animate-spin" />
                {buttonIsDeactivating ? "Deactivating…" : "Activating…"}
              </>
            ) : buttonIsDeactivating ? (
              <><Square size={11} /> Deactivate</>
            ) : (
              <><Play size={11} /> Activate</>
            )}
          </button>
        )}

        {isActive && isStarting && !deployPending && (
          <span className="flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full bg-blue-950 text-blue-400 ml-auto">
            <Loader2 size={11} className="animate-spin" /> Starting…
          </span>
        )}

        {!isStarting && chatUrl && (
          <a
            href={chatUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full bg-brand-500/10 text-brand-500 hover:bg-brand-500/20 transition-colors ml-auto"
            title="Open agent chat UI"
          >
            <ExternalLink size={11} /> Open Chat
          </a>
        )}
      </div>

      {/* Activation mode modal */}
      {activateModeOpen && (
        <ActivationModeModal
          activatingMode={activatingMode}
          onLocal={() => { setActivatingMode("local"); onActivateLocal?.(); }}
          onDocker={() => { setActivatingMode("docker"); onActivateDocker?.(); }}
          onClose={() => { setActivateModeOpen(false); setActivatingMode(null); }}
          error={undefined}
        />
      )}

      {/* Deactivate confirmation modal */}
      {confirmDeactivate && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-zinc-900 rounded-xl shadow-2xl w-full max-w-sm p-6 space-y-4">
            <div className="flex items-start gap-3">
              <div className="shrink-0 bg-amber-950 rounded-full p-2">
                <AlertTriangle size={20} className="text-amber-500" />
              </div>
              <div>
                <h3 className="font-semibold text-zinc-100">Deactivate agent?</h3>
                <p className="text-sm text-zinc-400 mt-1">
                  This will stop all running services for{" "}
                  <span className="font-medium text-zinc-200">{agent.name}</span>.
                  Active chat sessions will be disconnected.
                </p>
              </div>
            </div>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setConfirmDeactivate(false)}
                className="px-4 py-2 text-sm font-medium text-zinc-300 bg-zinc-800 hover:bg-zinc-700 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => { setConfirmDeactivate(false); onToggleDeploy?.(); }}
                className="px-4 py-2 text-sm font-medium text-white bg-amber-500 hover:bg-amber-600 rounded-lg transition-colors"
              >
                Deactivate
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
