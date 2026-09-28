export const skeletonThemes = [
  "catppuccin",
  "cerberus",
  "concord",
  "crimson",
  "dracula",
  "fennec",
  "hamlindigo",
  "legacy",
  "mint",
  "modern",
  "mona",
  "nosh",
  "nouveau",
  "pine",
  "reign",
  "rocket",
  "rose",
  "rosepine",
  "sahara",
  "seafoam",
  "terminus",
  "vintage",
  "vox",
  "wintry"
];

export function getStoredTheme() {
  if (typeof document === "undefined") return "wintry";

  const saved = window.localStorage.getItem("skeleton-theme");
  return saved && skeletonThemes.includes(saved) ? saved : "wintry";
}

export function applyTheme(themeName: string) {
  const safeTheme = skeletonThemes.includes(themeName) ? themeName : "wintry";

  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("data-theme", safeTheme);
    window.localStorage.setItem("skeleton-theme", safeTheme);
  }
}
