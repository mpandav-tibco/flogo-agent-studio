import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useState } from "react";
import { X } from "lucide-react";
import { createAgent, listTemplates } from "../api";
import type { Template } from "../types";

interface Props {
  onClose: () => void;
}

export default function TemplateModal({ onClose }: Props) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Template | null>(null);

  const { data: templates = [], isLoading } = useQuery({
    queryKey: ["templates"],
    queryFn: listTemplates,
  });

  const createMutation = useMutation({
    mutationFn: (tpl: Template) =>
      createAgent({
        name: tpl.name,
        description: tpl.description,
        config: { ...tpl.config },
      }),
    onSuccess: (agent) => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      navigate(`/agents/${agent.id}`);
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="bg-zinc-900 rounded-2xl shadow-2xl shadow-zinc-950 w-full max-w-2xl max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-zinc-800">
          <div>
            <h2 className="text-lg font-bold text-zinc-100">Start from a template</h2>
            <p className="text-sm text-zinc-500 mt-0.5">
              Pick a starting point — you can customise everything afterwards.
            </p>
          </div>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-200 transition-colors">
            <X size={20} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          {isLoading && (
            <p className="text-center text-zinc-500 text-sm py-8">Loading templates…</p>
          )}
          {!isLoading && templates.length === 0 && (
            <p className="text-center text-zinc-500 text-sm py-8">No templates available.</p>
          )}
          <div className="grid grid-cols-2 gap-3">
            {templates.map((tpl) => (
              <button
                key={tpl.id}
                onClick={() => setSelected(tpl)}
                className={`text-left rounded-xl border-2 p-4 transition-all ${selected?.id === tpl.id
                    ? "border-brand-500 bg-brand-500/10"
                    : "border-zinc-700 hover:border-zinc-500 bg-zinc-800 hover:bg-zinc-700"
                  }`}
              >
                <p className="font-semibold text-sm text-zinc-100">{tpl.name}</p>
                <p className="text-xs text-zinc-500 mt-0.5">{tpl.description}</p>
                {tpl.config.llmProvider && (
                  <p className="text-xs text-zinc-600 mt-1">{tpl.config.llmProvider}</p>
                )}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 px-6 py-4 border-t border-zinc-800">
          <button onClick={onClose} className="text-sm text-zinc-500 hover:text-zinc-300 transition-colors">
            Cancel
          </button>
          <div className="flex items-center gap-3">
            {createMutation.isError && (
              <p className="text-sm text-red-500">{String(createMutation.error)}</p>
            )}
            <button
              onClick={() => selected && createMutation.mutate(selected)}
              disabled={!selected || createMutation.isPending}
              className="flex items-center gap-2 bg-brand-500 hover:bg-brand-600 disabled:opacity-50 text-white text-sm font-medium px-5 py-2 rounded-lg transition-colors"
            >
              {createMutation.isPending ? "Creating…" : "Use this template →"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
