import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
    ArrowLeft,
    Database,
    RefreshCw,
    Trash2,
    ChevronDown,
    ChevronRight,
    AlertTriangle,
    Layers,
} from "lucide-react";
import { listKBCollections, getKBCollection, deleteKBCollection } from "../api";
import type { KBCollection } from "../api";
import ThemeToggle from "../components/ThemeToggle";

// ── Confirm dialog ─────────────────────────────────────────────────────────────
function ConfirmDeleteDialog({
    name,
    onConfirm,
    onCancel,
}: {
    name: string;
    onConfirm: () => void;
    onCancel: () => void;
}) {
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 max-w-sm w-full shadow-2xl">
                <div className="flex items-center gap-3 mb-4">
                    <AlertTriangle size={22} className="text-red-400 flex-shrink-0" />
                    <h2 className="text-white font-semibold text-lg">Delete Collection</h2>
                </div>
                <p className="text-gray-300 text-sm mb-6">
                    Permanently delete <span className="font-mono font-bold text-red-300">{name}</span>?
                    This removes all embedded documents and cannot be undone.
                </p>
                <div className="flex gap-3 justify-end">
                    <button
                        onClick={onCancel}
                        className="px-4 py-2 rounded-lg text-sm text-gray-300 bg-gray-800 hover:bg-gray-700 transition-colors"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={onConfirm}
                        className="px-4 py-2 rounded-lg text-sm text-white bg-red-600 hover:bg-red-500 transition-colors"
                    >
                        Delete
                    </button>
                </div>
            </div>
        </div>
    );
}

// ── Collection card ────────────────────────────────────────────────────────────
function CollectionCard({
    col,
    onDelete,
}: {
    col: KBCollection;
    onDelete: (name: string) => void;
}) {
    const [expanded, setExpanded] = useState(false);
    const qc = useQueryClient();

    const { data: detail, isLoading: detailLoading } = useQuery({
        queryKey: ["kb-collection-detail", col.name],
        queryFn: () => getKBCollection(col.name),
        enabled: expanded,
        staleTime: 30_000,
    });

    return (
        <div className="bg-gray-900 border border-gray-700 rounded-xl overflow-hidden">
            {/* Header row */}
            <div className="flex items-center justify-between px-5 py-4">
                <button
                    className="flex items-center gap-3 min-w-0 text-left flex-1 group"
                    onClick={() => setExpanded((v) => !v)}
                    aria-expanded={expanded}
                >
                    <Database size={18} className="text-indigo-400 flex-shrink-0 group-hover:text-indigo-300 transition-colors" />
                    <span className="font-mono font-medium text-gray-100 truncate group-hover:text-white transition-colors">
                        {col.name}
                    </span>
                    <span className="ml-auto flex-shrink-0 flex items-center gap-2 text-xs text-gray-400">
                        <Layers size={13} className="text-gray-500" />
                        {col.objectCount.toLocaleString()} docs
                        {expanded
                            ? <ChevronDown size={15} className="text-gray-500" />
                            : <ChevronRight size={15} className="text-gray-500" />
                        }
                    </span>
                </button>

                <button
                    onClick={() => onDelete(col.name)}
                    className="ml-4 flex-shrink-0 p-1.5 rounded-lg text-gray-500 hover:text-red-400 hover:bg-red-950/50 transition-colors"
                    title="Delete collection"
                    aria-label={`Delete ${col.name}`}
                >
                    <Trash2 size={16} />
                </button>
            </div>

            {/* Expandable detail */}
            {expanded && (
                <div className="border-t border-gray-700/60 bg-gray-800/50 px-5 py-4">
                    {detailLoading ? (
                        <p className="text-sm text-gray-400 animate-pulse">Loading details…</p>
                    ) : detail ? (
                        <div className="space-y-2">
                            <div className="flex items-baseline gap-2">
                                <span className="text-xs font-medium text-gray-400 uppercase tracking-wider w-28">Documents</span>
                                <span className="text-sm text-gray-100">{detail.objectCount.toLocaleString()}</span>
                            </div>
                            {detail.properties && detail.properties.length > 0 && (
                                <div className="flex items-start gap-2">
                                    <span className="text-xs font-medium text-gray-400 uppercase tracking-wider w-28 pt-0.5">Properties</span>
                                    <div className="flex flex-wrap gap-1.5">
                                        {detail.properties.map((p) => (
                                            <span
                                                key={p}
                                                className="bg-indigo-950 text-indigo-300 text-xs font-mono px-2 py-0.5 rounded-full"
                                            >
                                                {p}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            )}
                            <button
                                onClick={() => qc.invalidateQueries({ queryKey: ["kb-collection-detail", col.name] })}
                                className="text-xs text-indigo-400 hover:text-indigo-300 mt-1"
                            >
                                Refresh
                            </button>
                        </div>
                    ) : (
                        <p className="text-sm text-gray-400">No detail available.</p>
                    )}
                </div>
            )}
        </div>
    );
}

// ── Main page ──────────────────────────────────────────────────────────────────
export default function KnowledgeBase() {
    const navigate = useNavigate();
    const qc = useQueryClient();
    const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
    const [deleteError, setDeleteError] = useState<string | null>(null);

    const {
        data: collections = [],
        isLoading,
        isError,
        refetch,
        isFetching,
    } = useQuery<KBCollection[]>({
        queryKey: ["kb-collections"],
        queryFn: listKBCollections,
        staleTime: 15_000,
        refetchInterval: 30_000,
    });

    const deleteMutation = useMutation({
        mutationFn: (name: string) => deleteKBCollection(name),
        onSuccess: (_data, name) => {
            qc.invalidateQueries({ queryKey: ["kb-collections"] });
            qc.removeQueries({ queryKey: ["kb-collection-detail", name] });
            setConfirmDelete(null);
            setDeleteError(null);
        },
        onError: (err: Error) => {
            setDeleteError(err.message);
        },
    });

    const handleDeleteConfirm = () => {
        if (confirmDelete) deleteMutation.mutate(confirmDelete);
    };

    const totalDocs = collections.reduce((sum, c) => sum + c.objectCount, 0);

    return (
        <div className="min-h-screen bg-gray-950 text-gray-100">
            {/* Confirm dialog overlay */}
            {confirmDelete && (
                <ConfirmDeleteDialog
                    name={confirmDelete}
                    onConfirm={handleDeleteConfirm}
                    onCancel={() => { setConfirmDelete(null); setDeleteError(null); }}
                />
            )}

            {/* Top bar */}
            <header className="border-b border-gray-800 bg-gray-950/90 backdrop-blur-sm sticky top-0 z-10">
                <div className="max-w-5xl mx-auto px-6 py-4 flex items-center gap-4">
                    <button
                        onClick={() => navigate(-1)}
                        className="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
                        aria-label="Back"
                    >
                        <ArrowLeft size={18} />
                    </button>
                    <div className="flex items-center gap-2 flex-1">
                        <Database size={20} className="text-indigo-400" />
                        <h1 className="text-lg font-semibold text-white">Knowledge Base</h1>
                        <span className="ml-2 text-xs text-gray-500">
                            {collections.length} collection{collections.length !== 1 ? "s" : ""}
                            {totalDocs > 0 && ` · ${totalDocs.toLocaleString()} docs total`}
                        </span>
                    </div>
                    <button
                        onClick={() => refetch()}
                        disabled={isFetching}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-gray-300 bg-gray-800 hover:bg-gray-700 disabled:opacity-50 transition-colors"
                        title="Refresh"
                    >
                        <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} />
                        Refresh
                    </button>
                    <ThemeToggle />
                </div>
            </header>

            {/* Body */}
            <main className="max-w-5xl mx-auto px-6 py-8 space-y-4">
                {/* Delete error banner */}
                {deleteError && (
                    <div className="flex items-start gap-3 bg-red-950 border border-red-700 text-red-300 rounded-xl px-5 py-4 text-sm">
                        <AlertTriangle size={16} className="flex-shrink-0 mt-0.5" />
                        <div>
                            <strong>Delete failed:</strong> {deleteError}
                            <button
                                onClick={() => setDeleteError(null)}
                                className="ml-3 underline hover:no-underline"
                            >
                                Dismiss
                            </button>
                        </div>
                    </div>
                )}

                {isLoading ? (
                    <div className="space-y-3">
                        {[1, 2, 3].map((i) => (
                            <div key={i} className="h-16 bg-gray-800 rounded-xl animate-pulse" />
                        ))}
                    </div>
                ) : isError ? (
                    <div className="flex flex-col items-center justify-center py-20 text-center">
                        <AlertTriangle size={36} className="text-red-400 mb-3" />
                        <p className="text-gray-300 font-medium">Failed to load collections</p>
                        <p className="text-gray-500 text-sm mt-1">
                            Make sure the runtime manager is running at{" "}
                            <span className="font-mono text-indigo-400">:7050</span>
                        </p>
                        <button
                            onClick={() => refetch()}
                            className="mt-4 px-4 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-sm text-gray-200 transition-colors"
                        >
                            Retry
                        </button>
                    </div>
                ) : collections.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-20 text-center">
                        <Database size={40} className="text-gray-600 mb-4" />
                        <p className="text-gray-400 font-medium">No collections yet</p>
                        <p className="text-gray-600 text-sm mt-1">
                            Ingest documents through an agent to create a collection.
                        </p>
                    </div>
                ) : (
                    <div className="space-y-3">
                        {collections.map((col) => (
                            <CollectionCard
                                key={col.name}
                                col={col}
                                onDelete={(name) => { setDeleteError(null); setConfirmDelete(name); }}
                            />
                        ))}
                    </div>
                )}
            </main>
        </div>
    );
}
