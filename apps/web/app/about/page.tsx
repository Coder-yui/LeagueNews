import type { Metadata } from "next";
import { Bell, MessageCircle, Users } from "lucide-react";
import Image from "next/image";
import { PublicPageMasthead } from "@/components/public-page-masthead";
import { PublicShell } from "@/components/public-shell";

export const metadata: Metadata = {
  title: "关于",
  description: "认识 LeagueNews，并加入开发讨论或精选推送群。",
};

const communities = [
  {
    title: "LeagueNews 开发讨论群",
    description: "交流产品想法、功能建议、数据来源与后续方向。",
    platform: "飞书",
    image: "/images/community/feishu-development-qr.png",
    alt: "LeagueNews 开发讨论飞书群二维码",
    icon: MessageCircle,
    width: 700,
    height: 700,
  },
  {
    title: "LeagueNews 精选推送群",
    description: "接收 LeagueNews 整理发布的精选资讯推送。",
    platform: "飞书",
    image: "/images/community/feishu-featured-qr.png",
    alt: "LeagueNews 精选推送飞书群二维码",
    icon: Bell,
    width: 700,
    height: 700,
  },
  {
    title: "LeagueNews QQ 讨论群",
    description: "加入 QQ 群参与交流与反馈，群号 740019771。",
    platform: "QQ",
    image: "/images/community/qq-community-qr.jpg",
    alt: "LeagueNews QQ 讨论群二维码，群号 740019771",
    icon: Users,
    width: 1040,
    height: 1040,
  },
];

export default function AboutPage() {
  return (
    <PublicShell className="about-page">
      <PublicPageMasthead
        eyebrow="About LeagueNews"
        title="关于"
        description="一个持续整理英雄联盟消息、事件与日报的独立资讯项目。"
      />

      <section className="about-content public-frame">
        <div className="about-introduction">
          <p className="ln-eyebrow"><i /> 开发者的话</p>
          <h2>欢迎一起聊聊 LeagueNews</h2>
          <p>
            我是 LeagueNews 的开发者。如果你对这个项目感兴趣，欢迎加入群聊，和大家一起讨论产品、内容与后续方向。
          </p>
        </div>

        <div className="about-community-grid">
          {communities.map((community) => {
            const Icon = community.icon;
            return (
              <article className="about-community" key={community.title}>
                <div className="about-community-heading">
                  <span aria-hidden="true"><Icon size={17} strokeWidth={1.7} /></span>
                  <div>
                    <small>{community.platform}</small>
                    <h3>{community.title}</h3>
                  </div>
                </div>
                <p>{community.description}</p>
                <figure>
                  <Image
                    src={community.image}
                    alt={community.alt}
                    width={community.width}
                    height={community.height}
                    sizes="(max-width: 760px) calc(100vw - 48px), (max-width: 1080px) 50vw, 380px"
                  />
                </figure>
              </article>
            );
          })}
        </div>
      </section>
    </PublicShell>
  );
}
