from django.core.management.base import BaseCommand
from django.utils import timezone
from blog.models import BlogPost


SEED_POSTS = [
    {
        "title": "Why Remote Impact Jobs Matter in 2026",
        "slug": "why-remote-impact-jobs-matter-2026",
        "excerpt": "The intersection of remote work and purpose-driven careers is reshaping how we tackle the world's biggest challenges. Here's why remote impact jobs are more important than ever.",
        "body": """<p>The way we work has fundamentally shifted. By 2026, remote work isn't just a perk — it's the backbone of the global impact economy. Organizations fighting climate change, advancing AI safety, and improving global health are no longer limited to hiring within a 30-mile radius of their offices.</p>

<h2>The Remote Advantage for Impact Work</h2>
<p>Remote impact jobs unlock three powerful advantages:</p>
<ul>
<li><strong>Global talent pools:</strong> A climate nonprofit in San Francisco can now hire a brilliant data scientist in Nairobi or a policy expert in Berlin.</li>
<li><strong>Lower overhead, more impact:</strong> Organizations save on office costs and redirect those funds to their missions.</li>
<li><strong>Diverse perspectives:</strong> When your team spans continents, you get richer insights into the problems you're solving.</li>
</ul>

<h2>The Numbers Tell the Story</h2>
<p>Impact-focused job postings with remote options have grown 340% since 2020. Climate tech alone has seen a 5x increase in remote roles. And workers are responding — 78% of professionals under 35 say they'd take a pay cut to work on something meaningful, and 89% of those prefer remote or hybrid arrangements.</p>

<h2>What This Means for You</h2>
<p>Whether you're a software engineer wanting to apply your skills to clean energy, a marketer passionate about global health, or a project manager drawn to AI safety — there's never been a better time to find remote work that aligns with your values.</p>

<p>The future of impact is distributed, diverse, and digital. And it's hiring.</p>

<p><strong>Ready to find your role?</strong> <a href="/jobs/">Browse thousands of remote impact jobs</a> on Remote Impact.</p>""",
        "author_name": "Remote Impact Team",
    },
    {
        "title": "Top 10 Remote Climate Jobs You Can Apply to Today",
        "slug": "top-10-remote-climate-jobs",
        "excerpt": "From carbon accounting to clean energy engineering, these are the most in-demand remote climate jobs in 2026 — and how to land one.",
        "body": """<p>Climate tech is booming, and remote roles are at the heart of it. Whether you're a seasoned professional or career-switcher, here are the top 10 remote climate jobs hiring right now.</p>

<h2>1. Carbon Accounting Analyst</h2>
<p>Companies need experts to measure, report, and reduce their carbon footprints. Strong analytical skills and familiarity with GHG Protocol are key.</p>

<h2>2. Clean Energy Software Engineer</h2>
<p>Building the software that powers solar grids, wind farms, and battery storage systems. Python, data engineering, and energy domain knowledge are gold.</p>

<h2>3. Climate Policy Researcher</h2>
<p>Think tanks and advocacy organizations need remote researchers to analyze legislation, model policy impacts, and brief decision-makers.</p>

<h2>4. Sustainability Communications Manager</h2>
<p>Translating complex climate science into compelling stories for the public, investors, and policymakers.</p>

<h2>5. ESG Data Scientist</h2>
<p>Environmental, Social, and Governance investing is a trillion-dollar industry. Data scientists build the models that rate companies on their impact.</p>

<h2>6. Climate Product Manager</h2>
<p>Leading product development at climate tech startups — from EV charging platforms to carbon marketplace tools.</p>

<h2>7. Renewable Energy Project Coordinator</h2>
<p>Managing timelines, stakeholders, and budgets for solar and wind installations — increasingly done remotely with field teams on the ground.</p>

<h2>8. Climate Finance Analyst</h2>
<p>Evaluating investments in green bonds, carbon credits, and climate adaptation projects.</p>

<h2>9. Circular Economy Consultant</h2>
<p>Helping businesses redesign their supply chains to minimize waste and maximize reuse.</p>

<h2>10. Climate Education Designer</h2>
<p>Creating courses, curricula, and training programs that build climate literacy across industries.</p>

<h2>How to Stand Out</h2>
<p>The best candidates combine domain expertise with transferable skills. Don't have a climate background? Highlight your analytical, technical, or communication skills and show genuine passion for the space.</p>

<p><strong>Start your search:</strong> <a href="/jobs/?q=climate">Find remote climate jobs on Remote Impact</a>.</p>""",
        "author_name": "Remote Impact Team",
    },
    {
        "title": "How to Build an Impact Career from Anywhere",
        "slug": "build-impact-career-from-anywhere",
        "excerpt": "A practical guide to transitioning into purpose-driven remote work — whether you're just starting out or pivoting mid-career.",
        "body": """<p>You don't need to move to Washington D.C., San Francisco, or Geneva to build a career that matters. Here's a practical roadmap for building an impact career from wherever you are.</p>

<h2>Step 1: Define Your Impact Area</h2>
<p>Impact is broad. Start by identifying what you care about most:</p>
<ul>
<li><strong>Climate & environment:</strong> Clean energy, conservation, sustainable agriculture</li>
<li><strong>AI safety & governance:</strong> Ensuring advanced AI benefits humanity</li>
<li><strong>Global health:</strong> Pandemic preparedness, health equity, mental health</li>
<li><strong>Education & poverty:</strong> Access, literacy, economic development</li>
<li><strong>Animal welfare:</strong> Factory farming reform, wildlife protection</li>
</ul>

<h2>Step 2: Map Your Skills to Impact Roles</h2>
<p>Almost every skill is needed in the impact sector. Engineers build clean tech. Marketers amplify missions. Designers make complex data accessible. Operations people keep organizations running. Don't undersell your experience — reframe it.</p>

<h2>Step 3: Build Your Network (Remotely)</h2>
<p>Join communities like:</p>
<ul>
<li>80,000 Hours community</li>
<li>Climate Tech Handbook</li>
<li>Work on Climate Slack</li>
<li>EA Forum (for AI safety & global priorities)</li>
</ul>
<p>Attend virtual events, join Slack channels, and reach out to people doing work you admire. Remote networking is networking.</p>

<h2>Step 4: Start Before You're Ready</h2>
<p>Volunteer, freelance, or take on a side project in your target area. Write about what you're learning. Contribute to open-source impact projects. This builds credibility and helps you discover what fits.</p>

<h2>Step 5: Apply Strategically</h2>
<p>When you're ready to apply:</p>
<ul>
<li>Tailor every application to the organization's mission</li>
<li>Lead with impact in your cover letter, not just qualifications</li>
<li>Use our <a href="/tools/assistant/">AI Application Tools</a> to craft stronger applications</li>
<li>Apply to 5-10 roles per week, not 50</li>
</ul>

<h2>The Long Game</h2>
<p>Impact careers are marathons, not sprints. Give yourself permission to start where you are, learn as you go, and trust that meaningful work compounds over time.</p>

<p><strong>Your next chapter starts here:</strong> <a href="/jobs/">Explore remote impact jobs</a> and find work that matters.</p>""",
        "author_name": "Remote Impact Team",
    },
]


class Command(BaseCommand):
    help = "Seed the blog with initial posts"

    def handle(self, *args, **options):
        for data in SEED_POSTS:
            post, created = BlogPost.objects.update_or_create(
                slug=data["slug"],
                defaults={
                    "title": data["title"],
                    "excerpt": data["excerpt"],
                    "body": data["body"],
                    "author_name": data["author_name"],
                    "status": BlogPost.Status.PUBLISHED,
                    "published_at": timezone.now(),
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"{action}: {post.title}")

        self.stdout.write(self.style.SUCCESS(f"Done — {len(SEED_POSTS)} blog posts seeded."))
