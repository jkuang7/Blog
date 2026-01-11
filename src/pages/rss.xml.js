import rss from '@astrojs/rss';
import { getCollection } from 'astro:content';

export async function GET(context) {
  const posts = await getCollection('blog');
  const sortedPosts = posts
    .filter(post => !post.data.draft)
    .sort((a, b) => b.data.pubDate.valueOf() - a.data.pubDate.valueOf());

  return rss({
    title: "Jian Kuang's Blog",
    description: "Writing about software, engineering, and building things.",
    site: context.site,
    items: sortedPosts.map((post) => ({
      title: post.data.title,
      pubDate: post.data.pubDate,
      description: post.data.description,
      link: `/blog/${post.slug}/`,
      author: "jian@jiankuang.dev (Jian Kuang)",
    })),
    customData: `<language>en-us</language>
    <lastBuildDate>${new Date().toUTCString()}</lastBuildDate>
    <managingEditor>jian@jiankuang.dev (Jian Kuang)</managingEditor>
    <webMaster>jian@jiankuang.dev (Jian Kuang)</webMaster>`,
  });
}
