# 第二测试集：消息处理与事件聚合

- 样本：60 条；消息管线全部自动完成。其中发布 57 条，证据不足 2 条（RawItem 589, 690），判定为不相关 1 条（RawItem 601），待人工审核 0 条。
- 事件运行结果：`created=21`、`updated=13`、`multi_membership=2`、`not_event=21`。
- 活跃事件成员关系：44 条；复用运行前已有事件 12 条，新建事件成员 32 条。
- 至少进入已有事件的消息：11 条；至少进入新事件的消息：27 条；无事件成员：24 条。
- 有路由但最终没有成员：9 条，明细保留在文末供抽样检查。

## 已有事件复用明细

| 事件 ID | 聚合键 | 本批新增 RawItem |
|---:|---|---|
| 968 | `patch:lol_pc:26.13` | `248` |
| 970 | `release:lol_pc:经典模式` | `133`, `430` |
| 986 | `release:lol_pc:至臻熔岩大厅-维迦` | `139` |
| 994 | `matchday:lpl:2026-07-26` | `221`, `207` |
| 996 | `release:tft:云顶之弈s18` | `548` |
| 1001 | `activity:lol_pc:经典-战斗之夜` | `430` |
| 1002 | `matchday:lpl:2026-07-30` | `456`, `481`, `475` |
| 1005 | `cosmetic_batch:lol_pc:2026-07-30` | `369` |

## 逐条结果

| RawItem | 来源 | 分类 | 路由键 | 事件 ID | 成员类型 | 运行结果 | 预览 |
|---:|---|---|---|---|---|---|---|
| 75 | weibo | esports/match_schedule | matchday:lpl:2026-07-25 | 1020 | new | updated | 2026LPL第三赛段常规赛组内赛7月25日火热进行中，15:00 @LGD_英雄联盟 对战@WE电子竞技俱乐部 ，约1 |
| 78 | weibo | esports/match_result | matchday:lpl:2026-07-25 | 1020 | new | created | #2026LPL第三赛段# 常规赛组内赛W1D3每日TOP5：Knight隼舞索敌，暗影厮杀
TOP1：@Knight5 |
| 94 | weibo | esports/match_schedule | match:2026-07-25:ig-vs-wbg | - | - | not_event | 第一场登峰速战速决好吧！
涅槃大王之争！IG vs WBG 谁win？看手票型！
#Xiaohu喊话TheShy##Th |
| 123 | x_twitter | esports/match_schedule | matchday:lcs:2026-07-24 | - | - | not_event | RT @LCSOfficial: T-MINUS TWO HOURS UNTIL THE #LCS CLASSIC TS |
| 133 | weibo | game_mode/game_mode_release | release:lol_pc:经典模式 | 970 | existing | updated | #英雄联盟经典模式#即将上线，你还记得当年还有哪些出圈的梗吗？#英雄联盟经典模式来了# #英雄联盟[超话]# ​​​ |
| 139 | baidu_tieba | skin/skin_release | release:lol_pc:至臻熔岩大厅维迦;activity:lol_pc:2026年第三赛段第一幕 | 986 | existing | updated | 【皮肤鉴赏】至臻熔岩大厅维迦 |
| 162 | riot_official | community/community_event | activity:lol_pc:英雄联盟社区活动支持计划第二季 | 1019 | new | created | League of Legends NA Community Event Kits are Back! |
| 170 | x_twitter | community/community_post | - | - | - | not_event | RT @DYAMONDARTS: A little fan art for League Classic
I hope  |
| 196 | weibo | esports/match_result | matchday:lpl:2026-07-27 | 1021 | new | created | #2026LPL第三赛段# 常规赛组内赛W1D5每日TOP5：Heru至臻蛇女，石化凝视
TOP1：@TT_Heru ： |
| 205 | weibo | esports/community_post | - | - | - | not_event | 管泽元和足球解说王楚淇在讨论“谁才是BLG的问题”。
王楚淇：看到第五把，我就在好奇，现在BLG最大的区究竟是谁？很多人 |
| 207 | weibo | esports/match_result | match:2026-07-26:al-vs-blg | 994 | existing | updated | BLG 2：1 AL
下路突破，团战生猛，BLG豪取三连胜强势领跑积分榜！
#BLG保持全胜##Wenbo是对的##BL |
| 221 | weibo | esports/match_result | match:2026-07-26:al-vs-blg | 994 | existing | updated | Wenbo是对的啊？和想证明自己的呼吸哥对位也没落下风，队友Xun、Knight、Viper、On也很给力，BLG保持全 |
| 248 | x_twitter | patch/patch_preview | patch:lol_pc:26.13 | 968 | existing | updated | Patch 26.13 Full Preview!

Senna

- Senna is the big winner  |
| 279 | x_twitter | champion/champion_release | - | - | - | not_event | 3 more classic champions on pbe later today, surprised we're |
| 284 | x_twitter | tft/tft_set | release:tft:云顶之弈 | - | - | not_event | RT @RiotBlueVelvet: yo-

As most of you probably know TFT is |
| 295 | x_twitter | skin/skin_release | release:lol_pc:璐璐 | 1022 | new | created | Faerie Court Lulu Preview is now live on YouTube! youtu.be/c |
| 322 | weibo | esports/roster_move | transfer:2026:hope;transfer:2026:呼吸哥;transfer:2026:bin | - | - | not_event | 24年的遗憾应该是今年弥补吧，今年就是最有希望的一年！
从各方面角度都是补强吧，心态方面，战术体系方面，不稳定因素方面， |
| 332 | baidu_tieba | community/community_post | - | - | - | not_event | 经典模式 直播带货内容 |
| 337 | weibo | roster/roster_move | transfer:2026:moham | 1023 | new | created | 一年五辅成就达成！Erha Hang theHank jwei Monham！
WBG官宣了最后一名加盟的选手 Moha |
| 361 | weibo | esports/match_result | matchday:lpl:2026-07-29 | 1024 | new | created | BLG 1-1 LGD
LGD自身抓住机会！！！大龙逼团！布兜的闪现向前！heng盲僧的绕后一jio！
LGD拿下了BL |
| 369 | tencent_lol | activity/in_game_activity | activity_batch:lol_pc:2026-07-30;cosmetic_batch:lol_pc:2026-07-30;shop_offer:lol_pc:召唤师法球战利品礼包 | 1005,1026,1027 | existing+new | multi_membership | 幸运之门及第3赛季：第1幕通行证即将上线 |
| 402 | x_twitter | service/outage | incident:uncertain:2026-07-30:euw | - | - | not_event | EUW seems to be imploding, it truly is the League Classic ex |
| 413 | weibo | esports/match_schedule | match:2026-07-30:nip-vs-wbg;match:2026-07-30:edg-vs-we;match:2026-07-30:dk-vs-hle;match:2026-07-30:jdg-vs-tt | 1028,1029,1030,1031 | new | created | 今日赛程👇
15:00 NIP vs WBG（BO3）
17:00 EDG vs WE（BO3）
16:00 HLE v |
| 414 | baidu_tieba | commerce/shop_offer | shop_offer:lol_pc:永颂死歌 | 1025 | new | created | 【经典模式商城bug】永颂死歌价格仅13.5元 |
| 417 | weibo | tft/tft_set | release:tft:云顶s16;activity:tft:恭喜发财模式 | 1033,1034 | new | created | #英雄联盟经典模式来了# 就要饮血剑 🤷
从召唤师峡谷到云顶棋盘，经典装备带着熟悉的力量再次登场！#云顶S16回归#
恭 |
| 427 | baidu_tieba | commerce/shop_rotation | shop_rotation:lol_pc:cn:2026-W31 | 1032 | new | created | 【神话商城】07月30日双周更新：至臻猫咪、伊芙琳炫彩等 |
| 430 | weibo | game_mode/game_mode_release | release:lol_pc:经典模式;activity:lol_pc:英雄联盟经典战斗之夜 | 970,1001 | existing | updated | #LOL经典模式上线# 你还记得2013年的召唤师峡谷吗？ 那时候，草丛里永远藏着五个大汉，中路还流行着冥火之拥。召唤师 |
| 433 | baidu_tieba | game_mode/game_mode_release | release:uncertain:经典模式 | - | - | not_event | 经典模式来了 直播带货主题站 |
| 456 | weibo | esports/match_result | matchday:lpl:2026-07-30 | 1002 | existing | updated | #2026LPL第三赛段# 常规赛组内赛W2D2 #NIP对战WBG#
【NIP 1:0 WBG】恭喜@NIP电子竞技俱 |
| 458 | weibo | esports/community_post | - | - | - | not_event | 大黄：其实 wbg 这些人，每个人都是有顶天立地的实力，但是没办法，感觉就是这五个人凑到一起，他就是…垃圾。
大黄：这五 |
| 475 | weibo | esports/match_result | match:2026-07-30:jdg-vs-tt | 1002 | existing | updated | JDG 1-1 TT
Xiaoxu 对位爆了keshii，不过这中间怎么还是看着不太对劲！
JDG 扳回一城
#TT对战 |
| 476 | baidu_tieba | activity/free_reward | free_reward:tft:竹间清茗棋盘 | - | - | not_event | 【提醒】本次云顶赛季轮换，竹间清茗棋盘可兑换 |
| 477 | x_twitter | community/community_post | - | - | - | not_event | Yea..... not really sure if this is going to get anywhere. T |
| 481 | weibo | esports/match_result | matchday:lpl:2026-07-30 | 1002 | existing | updated | #2026LPL第三赛段# 常规赛组内赛W2D2 #TT对战JDG#
【TT 1:0 JDG】恭喜@TT英雄联盟分部 拿 |
| 499 | x_twitter | community/community_post | - | - | - | not_event | remembering the one time this happened |
| 502 | x_twitter | business/merch | merch:lol_merch_music:图奇 | - | - | not_event | RT @michellemauk: In honor of Classic, we have some new merc |
| 517 | x_twitter | community/community_post | - | - | - | not_event | In addition to Aurelion Sol for Reckoning, that looks like N |
| 527 | baidu_tieba | commerce/shop_rotation | shop_rotation:lol_pc:cn:2026-W31 | 1032 | new | updated | 7.31 神话商城 每日轮换 |
| 528 | weibo | esports/match_schedule | matchday:lpl:2026-07-31 | 1035 | new | created | #2026LPL第三赛段# 常规赛组内赛W2D3 赛事预告
硬碰硬！是豪取四连胜的BLG保持不败战绩稳固头名，还是斩获三 |
| 545 | weibo | esports/standings_qualification | - | - | - | not_event | 几家欢喜几家愁，现在EDG已经五连败了，大概率要掉入骑士之路，这登峰组的位置还没坐热乎呢[允悲]
另一边AL和BLG争登 |
| 548 | weibo | tft/tft_set | release:tft:云顶之弈s18;activity:tft:云顶之弈自然之力 | 996,1036 | existing+new | multi_membership | #云顶之弈S18# 培植丰茂的森林，向路遇的凡人传授奇特的智慧，这就是艾翁 [抱一抱]
携伴聚力，共赴自然奇境。8月27 |
| 555 | baidu_tieba | patch/hotfix | hotfix:lol_pc:2026-07-31 | 1038 | new | created | 2026年7月31日 不停机更新公告 |
| 562 | weibo | esports/match_result | match:2026-07-31:blg-vs-we | 1035 | new | updated | BLG 1-0 WE
Viper泽拉斯给压力太大！小半张地图直接支援开炮！手大哥中路也是一直能动！
WE一直被BLG拉扯 |
| 586 | x_twitter | activity/free_reward | free_reward:lol_pc:安妮图标 | 1037 | new | created | Redeem code CC-CLASS-ANNIE-T0123 now at riot.com/Classic to  |
| 589 | x_twitter | -/- | - | - | - | insufficient_evidence | It begins |
| 601 | x_twitter | -/- | - | - | - | irrelevant | Sweet, pulled a Chaos rune alt from my first Vendetta pack |
| 651 | x_twitter | skin/skin_release | release:lol_pc:奥瑞利安-索尔 | 1046 | new | created | Riftbound Reckoning will have Legends for Aurelion Sol, Cho’ |
| 658 | x_twitter | activity/free_reward | free_reward:lol_pc:胜利瑞兹 | 1039 | new | created | Triumphant Ryze is coming back as a reward skin for qualifyi |
| 667 | x_twitter | patch/item_rune_system | patch:lol_pc:1.5 | 1043 | new | created | Sunfire Aegis changes:
- Cost increased from 2700 to 2800
-  |
| 675 | x_twitter | commerce/shop_rotation | shop_rotation:lol_pc:global:2026-W32 | 1040 | new | created | Mythic Shop Rotation! |
| 682 | baidu_tieba | commerce/shop_rotation | shop_rotation:lol_pc:global:2026-W32 | 1040 | new | updated | 外服 守护者雕像瑞兹皮肤返场 |
| 690 | x_twitter | -/- | - | - | - | insufficient_evidence | wopsi |
| 706 | x_twitter | champion/champion_update | gameplay_update_batch:lol_pc:2026-08-04 | 1044 | new | created | Faerie Court Be'Veth Icon and Gwens icon got tweaked |
| 711 | x_twitter | champion/champion_update | gameplay:lol_pc:kenjamin | 1041 | new | created | Kenjamin buffs:
- R base damage per bolt increased from 40-1 |
| 723 | x_twitter | community/community_post | - | - | - | not_event | Sorry this came out 3 hours later than usual, seems my inter |
| 743 | x_twitter | champion/champion_update | gameplay:lol_pc:阿兹尔 | 1045 | new | created | Azir change, different from Phroxzon's:
- Q base damage chan |
| 750 | x_twitter | community/community_post | - | - | - | not_event | RT @Medaforcer: I did the concept art and splash for this pr |
| 753 | x_twitter | skin/skin_release | release:lol_pc:该死的披萨脚佛耶戈 | 1042 | new | created | Damn Pizza Feet Viego |
| 760 | x_twitter | esports/community_post | - | - | - | not_event | RT @LCK: UCAL AT IT AGAIN 😎 |
| 762 | x_twitter | esports/match_result | matchday:lck:2026-08-06 | - | - | not_event | RT @LCK: Clean sweep series for @T1LoL! #LCK |

## 有路由但未入事件

- RawItem `123`：`esports/match_schedule`，路由 `matchday:lcs:2026-07-24`，运行结果 `not_event`。
- RawItem `94`：`esports/match_schedule`，路由 `match:2026-07-25:ig-vs-wbg`，运行结果 `not_event`。
- RawItem `284`：`tft/tft_set`，路由 `release:tft:云顶之弈`，运行结果 `not_event`。
- RawItem `322`：`esports/roster_move`，路由 `transfer:2026:hope, transfer:2026:呼吸哥, transfer:2026:bin`，运行结果 `not_event`。
- RawItem `402`：`service/outage`，路由 `incident:uncertain:2026-07-30:euw`，运行结果 `not_event`。
- RawItem `433`：`game_mode/game_mode_release`，路由 `release:uncertain:经典模式`，运行结果 `not_event`。
- RawItem `476`：`activity/free_reward`，路由 `free_reward:tft:竹间清茗棋盘`，运行结果 `not_event`。
- RawItem `502`：`business/merch`，路由 `merch:lol_merch_music:图奇`，运行结果 `not_event`。
- RawItem `762`：`esports/match_result`，路由 `matchday:lck:2026-08-06`，运行结果 `not_event`。
