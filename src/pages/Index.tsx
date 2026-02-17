import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { compressSignal, extractKnowledge } from "@/lib/knowledge-engine";
import { saveStar, saveCard, getSettings } from "@/lib/store";
import type { Star, KnowledgeCard, StarLayers } from "@/types/star";

const DENSITY_OPTIONS = [25, 50, 75, 100] as const;
const LANGUAGES = [
  "English", "Español", "Français", "Deutsch", "Русский",
  "中文", "日本語", "العربية", "Português", "Italiano",
];
const LAYER_KEYS: (keyof StarLayers)[] = ["core", "structure", "depth", "resonance"];
const LAYER_LABELS: Record<keyof StarLayers, string> = {
  core: "Core",
  structure: "Structure",
  depth: "Depth",
  resonance: "Resonance",
};

const Index = () => {
  const navigate = useNavigate();
  const [input, setInput] = useState("");
  const [density, setDensity] = useState<number>(75);
  const [language, setLanguage] = useState("English");
  const [processing, setProcessing] = useState(false);
  const [result, setResult] = useState<{ star: Star; card: KnowledgeCard } | null>(null);
  const [activeLayer, setActiveLayer] = useState<keyof StarLayers>("core");
  const [saved, setSaved] = useState(false);

  const handleCompress = useCallback(async () => {
    if (!input.trim()) return;
    setProcessing(true);
    setSaved(false);
    setResult(null);

    await new Promise((r) => setTimeout(r, 800 + Math.random() * 600));

    const layers = compressSignal(input, density, language);
    const star: Star = {
      id: crypto.randomUUID(),
      inputText: input,
      densityLevel: density,
      language,
      layers,
      createdAt: new Date().toISOString(),
    };

    const card = extractKnowledge(input, layers, density, language, star.id);

    setResult({ star, card });
    setActiveLayer("core");
    setProcessing(false);

    const settings = getSettings();
    if (settings.autoSave) {
      saveStar(star);
      saveCard(card);
      setSaved(true);
    }
  }, [input, density, language]);

  const handleManualSave = () => {
    if (!result) return;
    saveStar(result.star);
    saveCard(result.card);
    setSaved(true);
  };

  return (
    <div className="min-h-screen bg-background relative overflow-hidden">
      <div className="fixed inset-0 cosmic-bg" />

      <div className="relative z-10 max-w-2xl mx-auto px-6 py-16 md:py-24">
        {/* Header */}
        <motion.header
          className="text-center mb-14"
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8 }}
        >
          <h1 className="text-3xl md:text-4xl font-bold tracking-tight text-foreground">
            Neutron Star
          </h1>
          <p className="text-primary/70 text-xs tracking-[0.35em] uppercase mt-2 font-mono">
            Signal Engine
          </p>
          <p className="text-muted-foreground text-xs mt-5 tracking-wide">
            Input chaos → Compress Signal → Stable insight → Human projection
          </p>
        </motion.header>

        {/* Input */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.2 }}
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Enter chaos here. Raw thoughts, fragments, unstructured signal..."
            className="w-full h-40 bg-input border border-border rounded-lg p-4 text-foreground placeholder:text-muted-foreground/40 resize-none focus:outline-none focus:ring-1 focus:ring-ring font-mono text-sm leading-relaxed transition-colors"
          />
        </motion.div>

        {/* Controls */}
        <motion.div
          className="flex flex-wrap items-center gap-6 mt-5"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.4 }}
        >
          <div className="flex items-center gap-3">
            <span className="text-[10px] text-muted-foreground uppercase tracking-widest">
              Density
            </span>
            <div className="flex gap-1">
              {DENSITY_OPTIONS.map((d) => (
                <button
                  key={d}
                  onClick={() => setDensity(d)}
                  className={`px-2.5 py-1 text-xs rounded-md transition-all ${
                    density === d
                      ? "bg-primary text-primary-foreground"
                      : "bg-secondary text-secondary-foreground hover:bg-muted"
                  }`}
                >
                  {d}%
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-[10px] text-muted-foreground uppercase tracking-widest">
              Lang
            </span>
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              className="bg-secondary text-secondary-foreground text-xs rounded-md px-2.5 py-1 border-none focus:outline-none focus:ring-1 focus:ring-ring cursor-pointer"
            >
              {LANGUAGES.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </div>
        </motion.div>

        {/* Compress Button */}
        <motion.div
          className="mt-10 flex justify-center"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5 }}
        >
          <Button
            size="lg"
            onClick={handleCompress}
            disabled={!input.trim() || processing}
            className={`glow-sm hover:glow-md transition-all px-8 ${
              processing ? "animate-glow-pulse" : ""
            }`}
          >
            {processing ? (
              <motion.span
                animate={{ opacity: [0.5, 1, 0.5] }}
                transition={{ duration: 1.5, repeat: Infinity }}
              >
                Compressing…
              </motion.span>
            ) : (
              "Compress Signal"
            )}
          </Button>
        </motion.div>

        {/* Output */}
        <AnimatePresence mode="wait">
          {result && (
            <motion.div
              initial={{ opacity: 0, y: 30 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.6 }}
              className="mt-14"
            >
              {/* Layer tabs */}
              <div className="flex gap-1 mb-4">
                {LAYER_KEYS.map((key) => (
                  <button
                    key={key}
                    onClick={() => setActiveLayer(key)}
                    className={`px-3 py-1.5 text-xs rounded-md transition-all ${
                      activeLayer === key
                        ? "bg-primary/10 text-primary border border-primary/20"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {LAYER_LABELS[key]}
                  </button>
                ))}
              </div>

              {/* Layer content */}
              <AnimatePresence mode="wait">
                <motion.div
                  key={activeLayer}
                  initial={{ opacity: 0, x: 8 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -8 }}
                  transition={{ duration: 0.25 }}
                  className="bg-card border border-border rounded-lg p-6 min-h-[120px]"
                >
                  <p className="text-foreground font-mono text-sm leading-relaxed whitespace-pre-wrap">
                    {result.star.layers[activeLayer]}
                  </p>
                </motion.div>
              </AnimatePresence>

              {/* Saved indicator */}
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.3 }}
                className="mt-5 flex items-center justify-center gap-2"
              >
                {saved ? (
                  <>
                    <span className="text-xs text-primary/60">✓ Saved</span>
                    <span className="text-xs text-muted-foreground/30">•</span>
                    <button
                      onClick={() => navigate("/vault")}
                      className="text-xs text-primary/60 hover:text-primary transition-colors underline-offset-2 hover:underline"
                    >
                      Open Vault
                    </button>
                  </>
                ) : (
                  <button
                    onClick={handleManualSave}
                    className="text-xs text-muted-foreground hover:text-primary transition-colors"
                  >
                    Save to Vault
                  </button>
                )}
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Vault nav */}
        <motion.div
          className="fixed bottom-6 right-6"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 1.2 }}
        >
          <button
            onClick={() => navigate("/vault")}
            className="text-[11px] text-muted-foreground/40 hover:text-primary/70 transition-colors font-mono"
          >
            vault →
          </button>
        </motion.div>
      </div>
    </div>
  );
};

export default Index;
