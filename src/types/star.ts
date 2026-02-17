export interface StarLayers {
  core: string;
  structure: string;
  depth: string;
  resonance: string;
}

export interface Star {
  id: string;
  inputText: string;
  densityLevel: number;
  language: string;
  layers: StarLayers;
  createdAt: string;
}

export interface Relationship {
  from: string;
  to: string;
  type: string;
  notes: string;
}

export interface KnowledgeCard {
  id: string;
  starId: string;
  title: string;
  coreClaim: string;
  uncertainty: {
    level: "certain" | "likely" | "uncertain";
    notes: string;
  };
  keyTerms: string[];
  relationships: Relationship[];
  implications: string[];
  patternLaw: string | null;
  domains: string[];
  language: string;
  createdAt: string;
}

export interface VaultSettings {
  autoSave: boolean;
}
