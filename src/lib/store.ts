import type { Star, KnowledgeCard, VaultSettings } from "@/types/star";

const STARS_KEY = "neutron-stars";
const CARDS_KEY = "neutron-cards";
const SETTINGS_KEY = "neutron-settings";

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function write<T>(key: string, data: T) {
  localStorage.setItem(key, JSON.stringify(data));
}

export function getStars(): Star[] {
  return read<Star[]>(STARS_KEY, []);
}
export function saveStar(star: Star) {
  const all = getStars();
  all.push(star);
  write(STARS_KEY, all);
}
export function deleteStar(id: string) {
  write(STARS_KEY, getStars().filter((s) => s.id !== id));
}
export function getStarById(id: string): Star | undefined {
  return getStars().find((s) => s.id === id);
}

export function getCards(): KnowledgeCard[] {
  return read<KnowledgeCard[]>(CARDS_KEY, []);
}
export function saveCard(card: KnowledgeCard) {
  const all = getCards();
  all.push(card);
  write(CARDS_KEY, all);
}
export function deleteCard(id: string) {
  write(CARDS_KEY, getCards().filter((c) => c.id !== id));
}

export function getSettings(): VaultSettings {
  return read<VaultSettings>(SETTINGS_KEY, { autoSave: true });
}
export function updateSettings(settings: Partial<VaultSettings>) {
  write(SETTINGS_KEY, { ...getSettings(), ...settings });
}
