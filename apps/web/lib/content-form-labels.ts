const contentFormLabels: Record<string, string> = {
  original: "原创",
  repost: "转发",
  quote: "引用",
  media_only: "仅媒体",
  link_only: "仅链接",
};

export function contentFormLabel(contentForm: string): string {
  return contentFormLabels[contentForm] ?? contentForm;
}
