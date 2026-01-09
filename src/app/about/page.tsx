"use client";

import { useEffect, useRef, useState } from "react";

interface TimelineEntry {
  period: string;
  role: string;
  company: string;
  story: string;
}

const timelineData: TimelineEntry[] = [
  {
    period: "2023-Present",
    role: "Senior Engineer",
    company: "Company A",
    story:
      "Took ownership of the frontend platform. Shipped a design system that cut dev time in half. Now mentoring the next generation.",
  },
  {
    period: "2021-2023",
    role: "Engineer",
    company: "Company B",
    story:
      "Joined a 5-person startup. Wore many hats—backend, frontend, infra. Learned what it takes to ship fast and iterate.",
  },
  {
    period: "2019-2021",
    role: "Junior Engineer",
    company: "Company C",
    story:
      "First real job. Wrote a lot of bad code, then learned to write good code. Found my love for clean architecture.",
  },
];

function TimelineItem({
  entry,
  index,
}: {
  entry: TimelineEntry;
  index: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [isVisible, setIsVisible] = useState(false);
  const [hasAnimated, setHasAnimated] = useState(false);
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);

  useEffect(() => {
    // Check reduced motion preference on mount
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    setPrefersReducedMotion(mediaQuery.matches);

    const handleChange = (e: MediaQueryListEvent) => {
      setPrefersReducedMotion(e.matches);
    };
    mediaQuery.addEventListener("change", handleChange);

    return () => mediaQuery.removeEventListener("change", handleChange);
  }, []);

  useEffect(() => {
    if (prefersReducedMotion) {
      // Skip animation, show immediately
      setIsVisible(true);
      setHasAnimated(true);
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
          setHasAnimated(true);
        } else {
          setIsVisible(false);
        }
      },
      { threshold: 0.3 }
    );

    if (ref.current) {
      observer.observe(ref.current);
    }

    return () => observer.disconnect();
  }, [prefersReducedMotion]);

  const baseClasses = prefersReducedMotion
    ? "opacity-100"
    : `transition-all duration-700 ease-out ${
        hasAnimated
          ? isVisible
            ? "opacity-100 translate-y-0"
            : "opacity-50 translate-y-0"
          : "opacity-0 translate-y-8"
      }`;

  return (
    <div
      ref={ref}
      className={`relative pl-8 pb-12 last:pb-0 ${baseClasses}`}
      style={{ transitionDelay: `${index * 100}ms` }}
    >
      {/* Timeline dot */}
      <div
        className={`absolute left-0 top-1 w-3 h-3 rounded-full border-2 transition-colors duration-300 ${
          isVisible
            ? "bg-blue-500 border-blue-500"
            : "bg-gray-300 dark:bg-gray-600 border-gray-300 dark:border-gray-600"
        }`}
      />

      {/* Timeline connector line */}
      <div className="absolute left-[5px] top-4 bottom-0 w-0.5 bg-gray-200 dark:bg-gray-700 last:hidden" />

      {/* Content */}
      <div className="space-y-1">
        <div className="text-sm text-gray-500 dark:text-gray-400 font-medium">
          {entry.period}
        </div>
        <div className="text-lg font-semibold text-gray-900 dark:text-white">
          {entry.role}{" "}
          <span className="font-normal text-gray-600 dark:text-gray-300">
            @ {entry.company}
          </span>
        </div>
        <p className="text-gray-600 dark:text-gray-300 leading-relaxed">
          {entry.story}
        </p>
      </div>
    </div>
  );
}

export default function AboutPage() {
  return (
    <main className="max-w-2xl mx-auto px-4 py-12">
      {/* Photo placeholder */}
      <div className="flex flex-col items-center mb-12">
        <div className="w-32 h-32 rounded-full bg-gray-200 dark:bg-gray-700 flex items-center justify-center mb-6">
          {/* Silhouette icon */}
          <svg
            className="w-16 h-16 text-gray-400 dark:text-gray-500"
            fill="currentColor"
            viewBox="0 0 24 24"
          >
            <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z" />
          </svg>
        </div>

        {/* Blurb */}
        <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-3">
          About Me
        </h1>
        <p className="text-gray-600 dark:text-gray-300 text-center max-w-md leading-relaxed">
          Software engineer based in the Bay Area. I build things that matter
          and write about what I learn along the way.
        </p>
      </div>

      {/* Timeline */}
      <section>
        <h2 className="text-xl font-bold text-gray-900 dark:text-white mb-8">
          Career Journey
        </h2>

        <div className="relative">
          {timelineData.map((entry, index) => (
            <TimelineItem key={entry.period} entry={entry} index={index} />
          ))}
        </div>
      </section>

      {/* Fallback: noscript shows all content */}
      <noscript>
        <style>{`.opacity-0 { opacity: 1 !important; } .translate-y-8 { transform: none !important; }`}</style>
      </noscript>
    </main>
  );
}
