import Link from "next/link";
import { getAllPostsMeta } from "@/lib/mdx";

export const metadata = {
  title: "Blog | Jian Kuang",
  description: "Technical case studies and engineering stories",
};

export default function BlogPage() {
  const posts = getAllPostsMeta();

  if (posts.length === 0) {
    return (
      <main className="max-w-4xl mx-auto px-4 py-12">
        <h1 className="text-3xl font-bold mb-8">Blog</h1>
        <p className="text-gray-600 dark:text-gray-400">
          No posts yet. Check back soon!
        </p>
      </main>
    );
  }

  return (
    <main className="max-w-4xl mx-auto px-4 py-12">
      <h1 className="text-3xl font-bold mb-8">Blog</h1>
      <div className="space-y-8">
        {posts.map((post) => (
          <article key={post.slug} className="group">
            <Link href={`/blog/${post.slug}`} className="block">
              <h2 className="text-xl font-semibold group-hover:text-blue-600 dark:group-hover:text-blue-400 mb-2">
                {post.title}
              </h2>
              <div className="flex items-center gap-4 text-sm text-gray-500 dark:text-gray-400 mb-2">
                <time dateTime={post.date}>
                  {new Date(post.date).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })}
                </time>
                {post.tags && post.tags.length > 0 && (
                  <div className="flex gap-2">
                    {post.tags.map((tag) => (
                      <span
                        key={tag}
                        className="px-2 py-0.5 bg-gray-100 dark:bg-gray-800 rounded text-xs"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
              {post.excerpt && (
                <p className="text-gray-600 dark:text-gray-400 line-clamp-2">
                  {post.excerpt}
                </p>
              )}
            </Link>
          </article>
        ))}
      </div>
    </main>
  );
}
