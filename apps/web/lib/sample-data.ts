import type { NewsEvent } from "./types";

export const sampleEvents: NewsEvent[] = [
  {
    id: 1,
    title: "版本 26.14 前瞻：打野生态与装备节奏调整",
    summary: "设计团队公布新一轮平衡方向，重点关注前期野区资源与部分战士装备。正式数值仍以版本公告为准。",
    category: "版本更新",
    entities: [{ name: "26.14", type: "patch" }, { name: "打野", type: "role" }],
    importance_score: 0.92,
    credibility: "official",
    occurred_at: "2026-07-10T03:00:00Z",
    created_at: "2026-07-10T03:10:00Z",
    items: []
  },
  {
    id: 2,
    title: "LPL 夏季赛今日焦点：积分区间竞争升温",
    summary: "多支队伍进入关键积分窗口，晚间对局将直接影响季后赛席位形势。",
    category: "赛事",
    entities: [{ name: "LPL", type: "league" }],
    importance_score: 0.84,
    credibility: "corroborated",
    occurred_at: "2026-07-10T06:00:00Z",
    created_at: "2026-07-10T06:20:00Z",
    items: []
  },
  {
    id: 3,
    title: "社区热议：经典系列皮肤或将迎来新成员",
    summary: "社区出现多条相关讨论，目前缺少官方素材或公告，暂按传闻观察。",
    category: "皮肤与活动",
    entities: [{ name: "新皮肤", type: "cosmetic" }],
    importance_score: 0.56,
    credibility: "rumor",
    occurred_at: null,
    created_at: "2026-07-10T07:40:00Z",
    items: []
  }
];
