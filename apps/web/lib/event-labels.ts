const lifecycleLabels: Record<string, string> = {
  scheduled: "未开始",
  live: "进行中",
  developing: "发展中",
  unconfirmed: "尚未确认",
  confirmed: "已确认",
  completed: "已结束",
  resolved: "已解决",
  disputed: "存在争议",
  expired_unconfirmed: "未证实结束",
  officially_refuted: "官方否认",
};

const credibilityLabels: Record<string, string> = {
  official_confirmed: "官方确认",
  multi_source_confirmed: "多源印证",
  single_source: "单源消息",
  unverified: "未经证实",
  disputed: "证据冲突",
  officially_refuted: "官方否认",
};

const eventTypeLabels: Record<string, string> = {
  patch: "版本",
  major_gameplay_change: "重大玩法改动",
  match: "比赛",
  transfer: "转会",
  roster: "阵容",
  release: "发布",
  activity: "活动",
  incident: "事件",
  tournament: "赛事节点",
  other: "资讯事件",
};

export function lifecycleLabel(value: string): string {
  return lifecycleLabels[value] ?? value;
}

export function credibilityLabel(value: string): string {
  return credibilityLabels[value] ?? value;
}

export function eventTypeLabel(value: string): string {
  return eventTypeLabels[value] ?? value;
}

export function importanceLevel(value: number): "high" | "medium" | "low" {
  if (value >= 0.8) return "high";
  if (value >= 0.5) return "medium";
  return "low";
}
