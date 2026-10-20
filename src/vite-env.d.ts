/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_ENABLE_ADS?: string;
  readonly VITE_SMART_LINK_URL?: string;
  readonly VITE_ENABLE_PREPLAY_AD?: string;
  readonly VITE_AD_COUNTDOWN_SECONDS?: string;
  readonly VITE_AD_BANNER_URL?: string;
  readonly VITE_AD_BANNER_IMAGE_URL?: string;
  readonly VITE_AD_BANNER_LABEL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
