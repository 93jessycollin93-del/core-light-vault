import { useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { getCards, deleteCard } from "@/lib/store";
import type { KnowledgeCard } from "@/types/star";

const DOMAIN_FILTERS = [
  "All", "Science", "Philosophy", "Psychology", "Technology",
  "Ecosystems", "Economics", "Culture", "Health", "Other",
];

const Section = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div>
    <p className="text-[10px] text-muted-foreground uppercase tracking-widest mb-1.5">{label}</p>
    {children}
  </div>
);

const Vault = () => {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [domainFilter, setDomainFilter] = useState("All");
  const [selectedCard, setSelectedCard] = useState<KnowledgeCard | null>(null);
  const [cards, setCards] = useState<KnowledgeCard[]>(() => getCards());

  const filteredCards = useMemo(() => {
    let result = cards;
    if (search) {
      const q = search.toLowerCase();
      result = result.filter(
        (c) =>
          c.title.toLowerCase().includes(q) ||
          c.coreClaim.toLowerCase().includes(q) ||
          c.keyTerms.some((t) => t.toLowerCase().includes(q)) ||
          c.domains.some((d) => d.toLowerCase().includes(q))
      );
    }
    if (domainFilter !== "All") {
      result = result.filter((c) =>
        c.domains.some((d) => d.toLowerCase() === domainFilter.toLowerCase())
      );
    }
    return result.sort(
      (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
    );
  }, [cards, search, domainFilter]);

  const exportAsText = () => {
    const text = filteredCards
      .map(
        (c) =>
          `📌 ${c.title}\n${c.coreClaim}${c.patternLaw ? `\n⚡ ${c.patternLaw}` : ""}\n🏷 ${c.domains.join(", ")}`
      )
      .join("\n\n---\n\n");
    navigator.clipboard.writeText(text);
  };

  const exportAsJSON = () => {
    const json = JSON.stringify(filteredCards, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "neutron-vault.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleDelete = (id: string) => {
    deleteCard(id);
    setCards(getCards());
    setSelectedCard(null);
  };

  const copyCard = (card: KnowledgeCard) => {
    navigator.clipboard.writeText(
      `${card.title}\n\n${card.coreClaim}${card.patternLaw ? `\n\n⚡ ${card.patternLaw}` : ""}\n\n${card.keyTerms.join(", ")}`
    );
  };

  return (
    <div className="min-h-screen bg-background relative overflow-hidden">
      <div className="fixed inset-0 cosmic-bg" />

      <div className="relative z-10 max-w-2xl mx-auto px-6 py-12">
        {/* Header */}
        <div className="flex items-start justify-between mb-8">
          <div>
            <button
              onClick={() => navigate("/")}
              className="text-xs text-muted-foreground hover:text-primary transition-colors mb-3 block font-mono"
            >
              ← signal engine
            </button>
            <h1 className="text-xl font-semibold text-foreground">Vault</h1>
            <p className="text-xs text-muted-foreground mt-1">
              {cards.length} knowledge atom{cards.length !== 1 ? "s" : ""} stored
            </p>
          </div>
          <div className="flex gap-2 mt-6">
            <button
              onClick={exportAsText}
              className="text-xs text-muted-foreground hover:text-primary transition-colors px-2.5 py-1.5 rounded-md bg-secondary"
            >
              Copy Text
            </button>
            <button
              onClick={exportAsJSON}
              className="text-xs text-muted-foreground hover:text-primary transition-colors px-2.5 py-1.5 rounded-md bg-secondary"
            >
              Export JSON
            </button>
          </div>
        </div>

        {/* Search */}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search knowledge…"
          className="w-full bg-input border border-border rounded-lg px-4 py-2.5 text-sm text-foreground placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-ring font-mono"
        />

        {/* Filters */}
        <div className="flex gap-1.5 mt-4 flex-wrap">
          {DOMAIN_FILTERS.map((d) => (
            <button
              key={d}
              onClick={() => setDomainFilter(d)}
              className={`px-2.5 py-1 text-xs rounded-full transition-all ${
                domainFilter === d
                  ? "bg-primary/15 text-primary border border-primary/20"
                  : "text-muted-foreground hover:text-foreground bg-secondary/50"
              }`}
            >
              {d}
            </button>
          ))}
        </div>

        {/* Card list */}
        <div className="mt-8 space-y-2">
          {filteredCards.length === 0 && (
            <p className="text-center text-muted-foreground text-sm py-16">
              {cards.length === 0
                ? "No knowledge stored yet. Compress some signal first."
                : "No matches found."}
            </p>
          )}
          {filteredCards.map((card, i) => (
            <motion.button
              key={card.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.04 }}
              onClick={() => setSelectedCard(card)}
              className="w-full text-left bg-card border border-border rounded-lg p-4 hover:border-primary/20 transition-colors"
            >
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <h3 className="text-sm font-medium text-foreground truncate">
                    {card.title}
                  </h3>
                  <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                    {card.coreClaim}
                  </p>
                </div>
                <span className="text-[10px] text-muted-foreground/40 ml-4 shrink-0">
                  {new Date(card.createdAt).toLocaleDateString()}
                </span>
              </div>
              <div className="flex gap-1.5 mt-2.5">
                {card.domains.slice(0, 3).map((d) => (
                  <span
                    key={d}
                    className="text-[10px] px-1.5 py-0.5 rounded bg-primary/5 text-primary/60"
                  >
                    {d}
                  </span>
                ))}
              </div>
            </motion.button>
          ))}
        </div>

        {/* Card detail modal */}
        <AnimatePresence>
          {selectedCard && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-50 flex items-center justify-center p-6"
              onClick={() => setSelectedCard(null)}
            >
              <div className="fixed inset-0 bg-background/80 backdrop-blur-sm" />
              <motion.div
                initial={{ opacity: 0, scale: 0.95, y: 20 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.95, y: 20 }}
                onClick={(e) => e.stopPropagation()}
                className="relative bg-card border border-border rounded-xl p-6 max-w-lg w-full max-h-[80vh] overflow-y-auto"
              >
                <button
                  onClick={() => setSelectedCard(null)}
                  className="absolute top-4 right-4 text-muted-foreground hover:text-foreground text-xs"
                >
                  ✕
                </button>

                <h2 className="text-lg font-semibold text-foreground pr-8">
                  {selectedCard.title}
                </h2>

                <div className="mt-5 space-y-5">
                  <Section label="Core Claim">
                    <p className="text-sm text-foreground/90 leading-relaxed">
                      {selectedCard.coreClaim}
                    </p>
                  </Section>

                  <Section label="Certainty">
                    <span
                      className={`text-xs px-2 py-0.5 rounded ${
                        selectedCard.uncertainty.level === "certain"
                          ? "bg-primary/10 text-primary"
                          : selectedCard.uncertainty.level === "likely"
                          ? "bg-accent/10 text-accent"
                          : "bg-destructive/10 text-destructive"
                      }`}
                    >
                      {selectedCard.uncertainty.level}
                    </span>
                    {selectedCard.uncertainty.notes && (
                      <p className="text-xs text-muted-foreground mt-1.5">
                        {selectedCard.uncertainty.notes}
                      </p>
                    )}
                  </Section>

                  <Section label="Key Terms">
                    <div className="flex flex-wrap gap-1.5">
                      {selectedCard.keyTerms.map((t) => (
                        <span
                          key={t}
                          className="text-xs px-2 py-0.5 rounded bg-secondary text-secondary-foreground"
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                  </Section>

                  {selectedCard.relationships.length > 0 && (
                    <Section label="Relationships">
                      <div className="space-y-1">
                        {selectedCard.relationships.map((r, i) => (
                          <p key={i} className="text-xs text-foreground/80 font-mono">
                            {r.from} →{" "}
                            <span className="text-primary/60">{r.type}</span> →{" "}
                            {r.to}
                          </p>
                        ))}
                      </div>
                    </Section>
                  )}

                  {selectedCard.implications.length > 0 && (
                    <Section label="Implications">
                      <div className="space-y-1">
                        {selectedCard.implications.map((imp, i) => (
                          <p key={i} className="text-xs text-foreground/80">
                            • {imp}
                          </p>
                        ))}
                      </div>
                    </Section>
                  )}

                  {selectedCard.patternLaw && (
                    <Section label="Pattern / Law">
                      <p className="text-sm text-accent font-mono">
                        ⚡ {selectedCard.patternLaw}
                      </p>
                    </Section>
                  )}

                  <Section label="Domains">
                    <div className="flex gap-1.5">
                      {selectedCard.domains.map((d) => (
                        <span
                          key={d}
                          className="text-[10px] px-1.5 py-0.5 rounded bg-primary/5 text-primary/60"
                        >
                          {d}
                        </span>
                      ))}
                    </div>
                  </Section>
                </div>

                {/* Actions */}
                <div className="mt-6 flex gap-2 border-t border-border pt-4">
                  <button
                    onClick={() => copyCard(selectedCard)}
                    className="text-xs text-muted-foreground hover:text-primary transition-colors px-3 py-1.5 rounded-md bg-secondary"
                  >
                    Copy
                  </button>
                  <button
                    onClick={() => handleDelete(selectedCard.id)}
                    className="text-xs text-destructive/60 hover:text-destructive transition-colors px-3 py-1.5 rounded-md bg-secondary"
                  >
                    Delete
                  </button>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
};

export default Vault;
