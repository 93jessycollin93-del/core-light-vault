import type { StarLayers, KnowledgeCard, Relationship } from "@/types/star";

const STOP_WORDS = new Set([
  "the","a","an","is","are","was","were","be","been","being","have","has","had",
  "do","does","did","will","would","could","should","may","might","can","shall",
  "to","of","in","for","on","with","at","by","from","as","into","through",
  "during","before","after","above","below","between","out","off","over","under",
  "again","further","then","once","here","there","when","where","why","how",
  "all","each","every","both","few","more","most","other","some","such",
  "no","not","only","own","same","so","than","too","very","just",
  "because","but","and","or","if","while","that","this","these","those",
  "it","its","they","them","their","we","us","our","you","your",
  "he","him","his","she","her","i","me","my","about","also","which","what",
  "who","whom","whose","like","much","many","well","back","even","still",
]);

const CAUSAL_WORDS = [
  "causes","leads","results","produces","creates","drives","influences",
  "affects","determines","shapes","triggers","enables","prevents",
  "increases","decreases","reduces","enhances","because","therefore",
  "consequently","thus","hence","since","due",
];

const DOMAIN_KEYWORDS: Record<string, string[]> = {
  Science: ["quantum","physics","biology","chemistry","evolution","molecule","atom","energy","entropy","thermodynamics","relativity","neuroscience","genetic","dna","cell","experiment","hypothesis","theory","empirical","data","research","study","observed"],
  Philosophy: ["consciousness","existence","meaning","truth","reality","metaphysics","epistemology","ethics","ontology","phenomenology","dualism","free will","determinism","nihilism","existentialism","being","essence","perception","moral"],
  Psychology: ["behavior","cognitive","emotion","memory","attention","motivation","personality","trauma","therapy","anxiety","depression","unconscious","perception","learning","conditioning","attachment","identity","self","mental"],
  Technology: ["algorithm","software","hardware","ai","machine learning","neural","network","data","computing","digital","automation","code","system","interface","protocol","encryption","blockchain"],
  Ecosystems: ["ecosystem","biodiversity","climate","environment","species","habitat","sustainability","ecology","carbon","pollution","conservation","deforestation","ocean","atmosphere","renewable"],
  Economics: ["market","economy","inflation","gdp","supply","demand","capital","investment","trade","fiscal","monetary","growth","recession","labor","productivity"],
  Culture: ["society","culture","tradition","art","music","language","identity","community","ritual","narrative","myth","symbol","media","communication","globalization"],
  Health: ["health","disease","medicine","treatment","diagnosis","symptom","immune","vaccine","nutrition","exercise","mental health","wellbeing","chronic","prevention"],
};

function extractSentences(text: string): string[] {
  return text
    .replace(/\n+/g, ". ")
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 10);
}

function extractKeyTerms(text: string): string[] {
  const words = text.toLowerCase().split(/[\s,;:()[\]{}'"]+/);
  const freq: Record<string, number> = {};

  words.forEach((w) => {
    const clean = w.replace(/[^a-záéíóúñüàèìòùâêîôûäëïöü]/gi, "");
    if (clean.length > 3 && !STOP_WORDS.has(clean)) {
      freq[clean] = (freq[clean] || 0) + 1;
    }
  });

  const caps = text.match(/[A-Z][a-z]{2,}/g) || [];
  caps.forEach((w) => {
    const lower = w.toLowerCase();
    if (!STOP_WORDS.has(lower)) {
      freq[lower] = (freq[lower] || 0) + 2;
    }
  });

  return Object.entries(freq)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
    .map(([word]) => word);
}

function detectDomains(text: string): string[] {
  const lower = text.toLowerCase();
  const scores: Record<string, number> = {};

  Object.entries(DOMAIN_KEYWORDS).forEach(([domain, keywords]) => {
    const score = keywords.reduce((acc, kw) => acc + (lower.includes(kw) ? 1 : 0), 0);
    if (score > 0) scores[domain] = score;
  });

  const sorted = Object.entries(scores).sort((a, b) => b[1] - a[1]);
  if (sorted.length === 0) return ["Other"];
  return sorted.slice(0, 3).map(([d]) => d);
}

function findRelationships(sentences: string[]): Relationship[] {
  const rels: Relationship[] = [];

  sentences.forEach((s) => {
    const lower = s.toLowerCase();
    for (const cw of CAUSAL_WORDS) {
      if (lower.includes(cw)) {
        const idx = lower.indexOf(cw);
        const before = s.slice(0, idx).trim().split(/\s+/).slice(-3).join(" ");
        const after = s.slice(idx + cw.length).trim().split(/\s+/).slice(0, 3).join(" ");
        if (before.length > 2 && after.length > 2) {
          rels.push({ from: before, to: after, type: cw, notes: s });
        }
        break;
      }
    }
  });

  return rels.slice(0, 5);
}

function findImplications(sentences: string[]): string[] {
  const markers = [
    "therefore","thus","hence","consequently","this means","this implies",
    "as a result","in practice","practically","should","must","need to",
    "important to","suggests that","indicates",
  ];

  return sentences
    .filter((s) => markers.some((m) => s.toLowerCase().includes(m)))
    .slice(0, 4);
}

function assessUncertainty(text: string): { level: "certain" | "likely" | "uncertain"; notes: string } {
  const lower = text.toLowerCase();
  const uncertainWords = ["maybe","perhaps","possibly","might","could","uncertain","unclear","debatable","controversial","speculative","hypothetical","theory","hypothesis","suggest","appear","seem"];
  const certainWords = ["proven","established","fact","evidence","demonstrates","confirms","clearly","definitively","certainly","always","never","law","rule","known"];

  const uncertainCount = uncertainWords.filter((w) => lower.includes(w)).length;
  const certainCount = certainWords.filter((w) => lower.includes(w)).length;

  if (certainCount > uncertainCount + 1) return { level: "certain", notes: "Strong assertion markers detected" };
  if (uncertainCount > certainCount + 1) return { level: "uncertain", notes: "Speculative or hedging language present" };
  return { level: "likely", notes: "Moderate confidence based on language analysis" };
}

function compressByDensity(text: string, density: number): string {
  const sentences = extractSentences(text);
  if (sentences.length === 0) return text.slice(0, 300);
  const keep = Math.max(1, Math.ceil(sentences.length * (1 - density / 120)));
  return sentences.slice(0, keep).join(" ");
}

function generatePatternLaw(coreClaim: string, keyTerms: string[]): string | null {
  if (coreClaim.length < 20) return null;
  const words = coreClaim.split(/\s+/);
  const meaningful = words.filter(
    (w) => !STOP_WORDS.has(w.toLowerCase().replace(/[^a-z]/g, "")) && w.length > 2
  );
  if (meaningful.length <= 2) return null;
  return meaningful.slice(0, 7).join(" ").replace(/[.,;:!?]+$/, "");
}

export function compressSignal(input: string, density: number, _language: string): StarLayers {
  const sentences = extractSentences(input);
  const keyTerms = extractKeyTerms(input);
  const rels = findRelationships(sentences);
  const implications = findImplications(sentences);
  const domains = detectDomains(input);
  const uncertainty = assessUncertainty(input);

  const densityFactor = density / 100;
  const coreCount = Math.max(1, Math.ceil(sentences.length * (1 - densityFactor * 0.7)));
  const core =
    sentences.length > 0
      ? sentences.slice(0, coreCount).join(" ")
      : input.slice(0, 200);

  const structure =
    rels.length > 0
      ? `${rels.length} causal link${rels.length > 1 ? "s" : ""} identified:\n\n${rels
          .map((r) => `${r.from} —[${r.type}]→ ${r.to}`)
          .join("\n")}\n\nKey elements: ${keyTerms.slice(0, 5).join(", ")}`
      : `Key elements: ${keyTerms.join(", ")}\n\n${sentences.length} propositions detected. No explicit causal chains found in input.`;

  const depth =
    implications.length > 0
      ? `Implications:\n\n${implications.map((imp, i) => `${i + 1}. ${imp}`).join("\n")}`
      : `Signal centers around: ${keyTerms.slice(0, 3).join(", ")}. Deeper causal analysis requires more structured input or explicit relational language.`;

  const resonance = `Domain resonance: ${domains.join(", ")}\nConfidence: ${uncertainty.level}\n\n${
    uncertainty.level === "uncertain"
      ? "This signal carries speculative energy — treat as hypothesis, not conclusion."
      : uncertainty.level === "certain"
      ? "This signal carries high conviction — grounded in assertive language."
      : "This signal balances assertion and exploration — remain open to revision."
  }\n\nPersistent terms: ${keyTerms.slice(0, 4).join(", ")}`;

  return { core, structure, depth, resonance };
}

export function extractKnowledge(
  input: string,
  _layers: StarLayers,
  density: number,
  language: string,
  starId: string
): KnowledgeCard {
  const sentences = extractSentences(input);
  const keyTerms = extractKeyTerms(input);
  const domains = detectDomains(input);
  const rels = findRelationships(sentences);
  const implications = findImplications(sentences);
  const uncertainty = assessUncertainty(input);

  const coreClaim = compressByDensity(input, density);
  const title =
    keyTerms
      .slice(0, 3)
      .map((t) => t.charAt(0).toUpperCase() + t.slice(1))
      .join(" · ") || "Untitled Signal";
  const patternLaw = generatePatternLaw(coreClaim, keyTerms);

  return {
    id: crypto.randomUUID(),
    starId,
    title,
    coreClaim,
    uncertainty,
    keyTerms,
    relationships: rels,
    implications:
      implications.length > 0
        ? implications
        : ["No explicit implications detected in input"],
    patternLaw,
    domains,
    language,
    createdAt: new Date().toISOString(),
  };
}
