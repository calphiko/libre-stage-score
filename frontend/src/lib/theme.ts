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

const defaultTheme = "rosepine";

export function getDefaultTheme() {
  return defaultTheme;
}

export function applyTheme(themeName: string) {
  const safeTheme = skeletonThemes.includes(themeName) ? themeName : defaultTheme;

  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("data-theme", safeTheme);
  }
}
