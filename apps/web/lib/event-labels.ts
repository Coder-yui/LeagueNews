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
  multi_source_supported: "多源支持",
  single_source: "单源消息",
  unverified: "未经证实",
  disputed: "证据冲突",
  officially_refuted: "官方否认",
};

const eventTypeLabels: Record<string, string> = {
  gameplay_update: "玩法更新",
  gameplay_release: "玩法发布",
  cosmetic_release: "外观发布",
  roster_change: "阵容变动",
  esports_match: "赛事对局",
  esports_schedule: "赛事日程",
  qualification_change: "晋级变化",
  commercial_offer: "商业内容",
  player_activity: "玩家活动",
  service_incident: "服务事件",
  disciplinary_action: "纪律处罚",
  security_notice: "安全公告",
  media_release: "媒体发布",
  corporate_announcement: "公司公告",
  community_activity: "社区活动",
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
