export interface CountryThemeProfile {
  accent: string;
  fill: string;
  halo: string;
  wallWarmth: string;
  loadingTone: string;
}

const DEFAULT_PROFILE: CountryThemeProfile = {
  accent: "#c99256",
  fill: "#88afc1",
  halo: "#e4c690",
  wallWarmth: "#e7ddcf",
  loadingTone: "#b87a48",
};

export function countryThemeProfile(country?: string | null): CountryThemeProfile {
  const normalized = (country ?? "").trim().toLowerCase();
  if (!normalized) {
    return DEFAULT_PROFILE;
  }
  if (normalized.includes("finland")) {
    return {
      accent: "#6b92b8",
      fill: "#9bb7cb",
      halo: "#dfeaf2",
      wallWarmth: "#d9e2ea",
      loadingTone: "#6c92bc",
    };
  }
  if (normalized.includes("switzerland")) {
    return {
      accent: "#b25f58",
      fill: "#a89483",
      halo: "#ead6cb",
      wallWarmth: "#e6ddd2",
      loadingTone: "#a85f54",
    };
  }
  if (normalized.includes("france")) {
    return {
      accent: "#7b96be",
      fill: "#b7c1cf",
      halo: "#ece6d8",
      wallWarmth: "#e7e1d8",
      loadingTone: "#7d92b4",
    };
  }
  if (normalized.includes("united states") || normalized === "us" || normalized === "u.s." || normalized === "usa") {
    return {
      accent: "#c99256",
      fill: "#82a9be",
      halo: "#e3c18b",
      wallWarmth: "#e8ddcf",
      loadingTone: "#b67847",
    };
  }
  return DEFAULT_PROFILE;
}
