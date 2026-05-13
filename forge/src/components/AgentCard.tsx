import { Copy, Pencil, Play, Square, Trash2 } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { cloneAgent, deleteAgent } from "../api";
import type { Agent } from "../types";

const STATUS_STYLES: Record<string, string> = {
  active:   "bg-green-100 text-green-800",
  draft:    "bg-gray-100 text-gray-600",
  archived: "bg-red-100 text-red-600",
};

interface AgentCardProps {
  agent: Agent;
  onToggleDeploy?: () => void;
  deployPending?: boolean;
}

export default function AgentCard({ agent, onToggleDeploy, deployPending }: AgentCardProps) {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const removeMutation = useMutation({
    mutationFn: () => deleteAgent(agent.id),
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

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-5 flex flex-col gap-3 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-gray-900 truncate">{agent.name}</h3>
        </div>
        <span
          className={`shrink-0 text-xs font-medium px-2 py-0.5 rounded-full ${STATUS_STYLES[agent.status] ?? STATUS_STYLES.draft}`}
        >
          {agent.status}
        </span>
      </div>

      {agent.description && (
        <p className="text-sm text-gray-600 line-clamp-2">{agent.description}</p>
      )}

      <div className="text-xs text-gray-400">
        {provider} · {model}
      </div>

      <div className="text-xs text-gray-300">
        v{agent.version} · updated {new Date(agent.updated_at).toLocaleDateString()}
      </div>

      <div className="flex gap-2 mt-auto pt-2 border-t border-gray-100">
        <button
          onClick={() => navigate(`/agents/${agent.id}`)}
          className="flex items-center gap-1 text-sm text-gray-600 hover:text-gray-900 px-2 py-1 rounded hover:bg-gray-100 transition-colors"
        >
          <Pencil size={14} /> Edit
        </button>

        <button
          onClick={() => cloneMutation.mutate()}
          disabled={cloneMutation.isPending}
          title="Duplicate this agent"
          className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800 px-2 py-1 rounded hover:bg-gray-100 transition-colors disabled:opacity-40"
        >
          <Copy size={14} /> {cloneMutation.isPending ? "…" : "Clone"}
        </button>

        {onToggleDeploy && (
          <button
            onClick={onToggleDeploy}
            disabled={deployPending}
            title={isActive ? "Deactivate agent" : "Activate agent"}
            className={`flex items-center gap-1 text-sm px-2 py-1 rounded transition-colors disabled:opacity-40 ${
              isActive
                ? "text-amber-600 hover:text-amber-800 hover:bg-amber-50"
                : "text-green-600 hover:text-green-800 hover:bg-green-50"
            }`}
          >
            {isActive ? <Square size={14} /> : <Play size={14} />}
            {isActive ? "Deactivate" : "Activate"}
          </button>
        )}

        <button
          onClick={() => {
            if (confirm(`Archive agent "${agent.name}"?`)) {
              removeMutation.mutate();
            }
          }}
          disabled={removeMutation.isPending}
          className="flex items-center gap-1 text-sm text-red-400 hover:text-red-600 px-2 py-1 rounded hover:bg-red-50 transition-colors ml-auto disabled:opacity-40"
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
}
