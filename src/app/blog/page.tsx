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
      <main className="max-w-3xl mx-auto px-4 py-12">
        <h1 className="text-3xl font-bold mb-8">Blog</h1>
        <p className="text-gray-600 dark:text-gray-400">
          No posts yet. Check back soon!
        </p>
      </main>
    );
  }

  return (
    <main className="max-w-3xl mx-auto px-4 py-12">
      <header className="mb-12">
        <h1 className="text-4xl font-bold mb-4">Blog</h1>
        <p className="text-gray-600 dark:text-gray-400 text-lg">
          Technical case studies and engineering stories from my career.
        </p>
      </header>
      <div className="space-y-10">
        {posts.map((post) => (
          <article
            key={post.slug}
            className="group border-b border-gray-100 dark:border-gray-800 pb-10 last:border-0"
          >
            <Link href={`/blog/${post.slug}`} className="block">
              <h2 className="text-2xl font-semibold group-hover:text-blue-600 dark:group-hover:text-blue-400 mb-3 transition-colors">
                {post.title}
              </h2>
              <div className="flex flex-wrap items-center gap-3 text-sm text-gray-500 dark:text-gray-400 mb-3">
                <time dateTime={post.date}>
                  {new Date(post.date).toLocaleDateString("en-US", {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })}
                </time>
                {post.readingTime && (
                  <>
                    <span className="text-gray-300 dark:text-gray-600">•</span>
                    <span>{post.readingTime} min read</span>
                  </>
                )}
              </div>
              {post.excerpt && (
                <p className="text-gray-600 dark:text-gray-400 leading-relaxed mb-4">
                  {post.excerpt}
                </p>
              )}
              {post.tags && post.tags.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {post.tags.map((tag) => (
                    <span
                      key={tag}
                      className="px-2.5 py-1 bg-gray-100 dark:bg-gray-800 rounded-full text-xs font-medium text-gray-600 dark:text-gray-400"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </Link>
          </article>
        ))}
      </div>
    </main>
  );
}
