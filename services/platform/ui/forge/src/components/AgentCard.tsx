import { useEffect, useRef, useState } from "react";
import { ExternalLink, Loader2, MoreHorizontal, Play, Square } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { cloneAgent, deleteAgent, purgeAgent } from "../api";
import type { Agent } from "../types";


const STATUS_STYLES: Record<string, string> = {
  active:   "bg-green-100 text-green-800",
  starting: "bg-blue-100 text-blue-700",
  draft:    "bg-slate-100 text-slate-600",
  archived: "bg-red-50 text-red-600",
};

const STATUS_BORDER: Record<string, string> = {
  active: "border-l-green-400",
  draft: "border-l-slate-300",
  archived: "border-l-red-300",
};

interface AgentCardProps {
  agent: Agent;
  onToggleDeploy?: () => void;
  deployPending?: boolean;
  /** True while deployment.py is still spinning up this agent's services. */
  isStarting?: boolean;
}

export default function AgentCard({ agent, onToggleDeploy, deployPending, isStarting }: AgentCardProps) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

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
  const borderClass = STATUS_BORDER[agent.status] ?? STATUS_BORDER.draft;
  // chatUrl is only set once deployment.py has patched chatUiUrl into design-service
  // (which now happens only after all services are confirmed ready).
  const chatUrl = isActive ? (agent.config?.chatUiUrl || null) : null;

  return (
    <div
      className={`bg-white rounded-xl shadow-sm border border-gray-200 border-l-4 ${borderClass} flex flex-col hover:shadow-md transition-shadow cursor-pointer`}
      onClick={() => navigate(`/agents/${agent.id}`)}
    >
      {/* Card body */}
      <div className="p-5 flex flex-col gap-2 flex-1">
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-semibold text-gray-900 truncate flex-1">{agent.name}</h3>

          {/* Status badge + overflow menu — stop propagation so click doesn't open editor */}
          <div className="flex items-center gap-1.5 shrink-0" onClick={(e) => e.stopPropagation()}>
            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${STATUS_STYLES[isStarting ? "starting" : agent.status] ?? STATUS_STYLES.draft}`}>
              {isStarting ? "starting" : agent.status}
            </span>

            <div className="relative" ref={menuRef}>
              <button
                onClick={(e) => { e.stopPropagation(); setMenuOpen((o) => !o); }}
                className="text-gray-400 hover:text-gray-700 p-0.5 rounded hover:bg-gray-100 transition-colors"
                title="More options"
              >
                <MoreHorizontal size={15} />
              </button>

              {menuOpen && (
                <div className="absolute right-0 top-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-10 min-w-[128px] py-1">
                  <button
                    onClick={(e) => { e.stopPropagation(); setMenuOpen(false); navigate(`/agents/${agent.id}`); }}
                    className="w-full text-left text-sm px-3 py-1.5 hover:bg-gray-50 text-gray-700"
                  >
                    Edit
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setMenuOpen(false); cloneMutation.mutate(); }}
                    disabled={cloneMutation.isPending}
                    className="w-full text-left text-sm px-3 py-1.5 hover:bg-gray-50 text-gray-600 disabled:opacity-40"
                  >
                    {cloneMutation.isPending ? "Cloning…" : "Clone"}
                  </button>
                  <div className="border-t border-gray-100 my-1" />
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      setMenuOpen(false);
                      if (confirm(`Archive agent "${agent.name}"?`)) removeMutation.mutate();
                    }}
                    disabled={removeMutation.isPending || agent.status === "archived"}
                    className="w-full text-left text-sm px-3 py-1.5 hover:bg-red-50 text-red-500 disabled:opacity-40"
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
                      className="w-full text-left text-sm px-3 py-1.5 hover:bg-red-100 text-red-700 font-medium disabled:opacity-40"
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
          <p className="text-sm text-gray-500 line-clamp-2">{agent.description}</p>
        )}

        <div className="mt-auto pt-1 space-y-0.5">
          <div className="text-xs text-gray-400">{provider} · {model}</div>
          <div className="text-xs text-gray-300">v{agent.version} · {new Date(agent.updated_at).toLocaleDateString()}</div>
        </div>
      </div>

      {/* Footer actions — stop propagation so clicks don't open editor */}
      <div
        className="border-t border-gray-100 px-4 py-2.5 flex items-center gap-2"
        onClick={(e) => e.stopPropagation()}
      >
        {onToggleDeploy && (
          <button
            onClick={onToggleDeploy}
            disabled={deployPending || isStarting}
            className={`flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full transition-colors disabled:opacity-40 ${isActive
              ? "bg-amber-50 text-amber-700 hover:bg-amber-100"
              : "bg-green-50 text-green-700 hover:bg-green-100"
              }`}
          >
            {isActive ? <><Square size={11} /> Deactivate</> : <><Play size={11} /> Activate</>}
          </button>
        )}

        {/* Show spinner while services are starting; Open Chat only once chatUiUrl is patched */}
        {isActive && isStarting && (
          <span className="flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full bg-blue-50 text-blue-600 ml-auto">
            <Loader2 size={11} className="animate-spin" /> Starting…
          </span>
        )}

        {!isStarting && chatUrl && (
          <a
            href={chatUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-xs font-medium px-2.5 py-1 rounded-full bg-brand-50 text-brand-700 hover:bg-brand-100 transition-colors ml-auto"
            title="Open agent chat UI"
          >
            <ExternalLink size={11} /> Open Chat
          </a>
        )}
      </div>
    </div>
  );
}
