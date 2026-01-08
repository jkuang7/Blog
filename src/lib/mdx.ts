import fs from "fs";
import path from "path";
import matter from "gray-matter";
import { compileMDX } from "next-mdx-remote/rsc";
import rehypePrettyCode from "rehype-pretty-code";

const contentDir = path.join(process.cwd(), "content/blog");

export interface PostFrontmatter {
  title: string;
  date: string;
  tags: string[];
  slug: string;
  excerpt?: string;
  placeholder?: boolean;
  storyRef?: string;
}

export interface PostMeta extends PostFrontmatter {
  slug: string;
}

export interface Post {
  meta: PostMeta;
  content: React.ReactElement;
}

/**
 * Get all MDX files from content/blog
 */
export function getPostSlugs(): string[] {
  if (!fs.existsSync(contentDir)) {
    return [];
  }
  return fs
    .readdirSync(contentDir)
    .filter((file) => file.endsWith(".mdx"))
    .map((file) => file.replace(/\.mdx$/, ""));
}

/**
 * Get frontmatter for all posts (for list page)
 */
export function getAllPostsMeta(): PostMeta[] {
  const slugs = getPostSlugs();
  const posts = slugs.map((slug) => {
    const filePath = path.join(contentDir, `${slug}.mdx`);
    const fileContents = fs.readFileSync(filePath, "utf8");
    const { data } = matter(fileContents);

    return {
      ...(data as PostFrontmatter),
      slug,
    };
  });

  // Sort by date descending
  return posts.sort(
    (a, b) => new Date(b.date).getTime() - new Date(a.date).getTime()
  );
}

/**
 * Get a single post by slug with compiled MDX content
 */
export async function getPostBySlug(slug: string): Promise<Post | null> {
  const filePath = path.join(contentDir, `${slug}.mdx`);

  if (!fs.existsSync(filePath)) {
    return null;
  }

  const fileContents = fs.readFileSync(filePath, "utf8");
  const { data, content: rawContent } = matter(fileContents);

  const { content } = await compileMDX({
    source: rawContent,
    options: {
      parseFrontmatter: false,
      mdxOptions: {
        rehypePlugins: [
          [
            rehypePrettyCode,
            {
              theme: "github-dark",
              keepBackground: true,
            },
          ],
        ],
      },
    },
  });

  return {
    meta: {
      ...(data as PostFrontmatter),
      slug,
    },
    content,
  };
}
