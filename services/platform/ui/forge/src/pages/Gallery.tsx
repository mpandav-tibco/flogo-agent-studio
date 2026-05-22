import { useEffect, useRef, useState } from "react";
import { Activity, LayoutTemplate, Plus, Search } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { deployAgent, getAgentRuntime, listAgents, undeployAgent } from "../api";
import AgentCard from "../components/AgentCard";
import TemplateModal from "../components/TemplateModal";

const STATUS_TABS = ["All", "active", "draft", "archived"] as const;
type StatusTab = typeof STATUS_TABS[number];

export default function Gallery() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [showTemplates, setShowTemplates] = useState(false);
  const [activeTab, setActiveTab] = useState<StatusTab>("All");
  const [searchQuery, setSearchQuery] = useState("");

  // Backend does not filter by status — always fetch all, filter client-side
  const { data: allAgents = [], isLoading, error } = useQuery({
    queryKey: ["agents"],
    queryFn: () => listAgents(),
  });

  const deployMutation = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      active ? undeployAgent(id) : deployAgent(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });

  // Derive IDs of all active agents from the server list (survives page refresh)
  const activeAgentIds = allAgents.filter((a) => a.status === "active").map((a) => a.id);

  // Persistently poll runtime-manager readiness for every active agent.
  // Polls every 3 s while any agent is starting; 15 s otherwise.
  const { data: runtimeStates } = useQuery({
    queryKey: ["runtimeStates", [...activeAgentIds].sort().join(",")],
    queryFn: async () => {
      const results = await Promise.allSettled(
        activeAgentIds.map(async (id) => {
          const rt = await getAgentRuntime(id);
          return [id, rt?.readiness ?? "starting"] as [string, string];
        })
      );
      return Object.fromEntries(
        results
          .filter((r): r is PromiseFulfilledResult<[string, string]> => r.status === "fulfilled")
          .map((r) => r.value)
      );
    },
    enabled: activeAgentIds.length > 0,
    refetchInterval: (query) => {
      const data = query.state.data as Record<string, string> | undefined;
      const anyStarting = data && Object.values(data).some((s) => s === "starting");
      return anyStarting ? 3000 : 15000;
    },
    staleTime: 1000,
  });

  // When an agent transitions from "starting" → "ready"/"degraded", re-fetch the
  // agents list so chatUiUrl and other patched fields are picked up from design-service.
  const runtimeRef = useRef<Record<string, string>>({});
  useEffect(() => {
    if (!runtimeStates) return;
    const transitions = Object.entries(runtimeStates).filter(
      ([id, s]) =>
        (s === "ready" || s === "degraded") && runtimeRef.current[id] === "starting"
    );
    runtimeRef.current = { ...runtimeStates };
    if (transitions.length > 0) {
      qc.invalidateQueries({ queryKey: ["agents"] });
    }
  }, [runtimeStates, qc]);

  // An agent is "starting" when it's active but runtime hasn't confirmed "ready"/"degraded" yet
  const isAgentStarting = (id: string): boolean => {
    if (!activeAgentIds.includes(id)) return false;
    const r = runtimeStates?.[id];
    return r !== "ready" && r !== "degraded";
  };

  const agentsByTab: Record<StatusTab, typeof allAgents> = {
    All: allAgents.filter((a) => a.status !== "archived"),
    active: allAgents.filter((a) => a.status === "active"),
    draft: allAgents.filter((a) => a.status === "draft"),
    archived: allAgents.filter((a) => a.status === "archived"),
  };

  const visible = searchQuery.trim()
    ? agentsByTab[activeTab].filter((a) =>
      a.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      a.description?.toLowerCase().includes(searchQuery.toLowerCase())
    )
    : agentsByTab[activeTab];

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <img src="/logo.svg" alt="AgentForge Studio" className="h-14" />
          <div className="flex items-center gap-2">
            <button
              onClick={() => navigate("/admin")}
              className="flex items-center gap-2 text-sm font-medium text-gray-600 hover:text-gray-900 border border-gray-200 hover:border-gray-400 px-4 py-2 rounded-lg transition-colors"
            >
              <Activity size={15} />
              Admin
            </button>
            <button
              onClick={() => setShowTemplates(true)}
              className="flex items-center gap-2 text-sm font-medium text-gray-600 hover:text-gray-900 border border-gray-200 hover:border-gray-400 px-4 py-2 rounded-lg transition-colors"
            >
              <LayoutTemplate size={15} />
              Templates
            </button>
            <button
              onClick={() => navigate("/agents/new")}
              className="flex items-center gap-2 bg-brand-500 hover:bg-brand-600 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
              <Plus size={16} />
              New Agent
            </button>
          </div>
        </div>

        {/* Status filter tabs + search */}
        <div className="max-w-6xl mx-auto mt-3 flex items-center gap-2">
          <div className="flex gap-1">
            {STATUS_TABS.map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`flex items-center gap-1.5 text-sm px-3 py-1 rounded-md capitalize transition-colors ${activeTab === tab
                  ? "bg-brand-500 text-white"
                  : "text-gray-500 hover:text-gray-800 hover:bg-gray-100"
                  }`}
              >
                {tab}
                {!isLoading && (
                  <span className={`text-xs px-1.5 py-0.5 rounded-full leading-none ${activeTab === tab ? "bg-white/25 text-white" : "bg-gray-200 text-gray-500"
                    }`}>
                    {agentsByTab[tab].length}
                  </span>
                )}
              </button>
            ))}
          </div>

          <div className="ml-auto relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search agents…"
              className="text-sm border border-gray-200 rounded-lg pl-7 pr-3 py-1 focus:outline-none focus:ring-2 focus:ring-brand-500 w-44 bg-white"
            />
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8">
        {isLoading && (
          <div className="text-center text-gray-400 py-16">Loading agents…</div>
        )}

        {error && (
          <div className="text-center text-red-500 py-16">
            <p>Could not load agents — {(error as Error).message || "is design-service running on port 7020?"}</p>
          </div>
        )}

        {!isLoading && !error && visible.length === 0 && (
          <div className="text-center py-24">
            <div className="text-5xl mb-4">🤖</div>
            <h2 className="text-xl font-semibold text-gray-700 mb-2">
              {searchQuery ? `No results for "${searchQuery}"` : activeTab === "All" ? "No agents yet" : `No ${activeTab} agents`}
            </h2>
            <p className="text-gray-500 mb-6">
              {searchQuery ? "Try a different search term." : activeTab === "All" ? "Create your first agent or start from a template." : activeTab === "active" ? "Activate an agent from the Draft tab to see it here." : activeTab === "archived" ? "Archived agents will appear here." : "Create your first agent or start from a template."}
            </p>
            <div className="flex items-center justify-center gap-3">
              <button
                onClick={() => setShowTemplates(true)}
                className="inline-flex items-center gap-2 border border-gray-300 hover:border-gray-400 text-gray-700 text-sm font-medium px-5 py-2.5 rounded-lg transition-colors"
              >
                <LayoutTemplate size={15} />
                Browse Templates
              </button>
              <button
                onClick={() => navigate("/agents/new")}
                className="inline-flex items-center gap-2 bg-brand-500 hover:bg-brand-600 text-white text-sm font-medium px-5 py-2.5 rounded-lg transition-colors"
              >
                <Plus size={16} />
                Create from Scratch
              </button>
            </div>
          </div>
        )}

        {visible.length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
            {visible.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                onToggleDeploy={() =>
                  deployMutation.mutate({ id: agent.id, active: agent.status === "active" })
                }
                deployPending={deployMutation.isPending && deployMutation.variables?.id === agent.id}
                isStarting={isAgentStarting(agent.id)}
              />
            ))}
          </div>
        )}
      </main>

      {showTemplates && <TemplateModal onClose={() => setShowTemplates(false)} />}
    </div>
  );
}
