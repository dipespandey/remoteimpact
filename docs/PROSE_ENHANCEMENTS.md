# Prose Enhancements Guide

This document explains how to use the visual enhancement components for text-heavy pages like pillars, clusters, and blog posts.

## Quick Start

Add this to your template's `extra_head` block:
```django
{% block extra_head %}
{% include 'components/prose_enhancements.html' %}
{% endblock %}
```

## Available Components

### 1. Stats Row
Display key numbers with gradient styling:

```html
<div class="stats-row not-prose">
    <div class="stat-card">
        <div class="stat-number">$1.4T</div>
        <div class="stat-label">Annual Funding</div>
    </div>
    <div class="stat-card">
        <div class="stat-number">38%</div>
        <div class="stat-label">Job Growth</div>
    </div>
    <div class="stat-card">
        <div class="stat-number">$95K</div>
        <div class="stat-label">Avg Salary</div>
    </div>
</div>
```

### 2. TL;DR / Key Takeaways Box
Summarize main points at the top:

```html
<div class="tldr-box not-prose">
    <h4>⚡ Quick Summary</h4>
    <ul>
        <li>Point one</li>
        <li>Point two</li>
        <li>Point three</li>
    </ul>
</div>
```

### 3. Table of Contents
Link to sections with anchor links:

```html
<div class="toc-box not-prose">
    <h4>In This Guide</h4>
    <ol>
        <li><a href="#section1">Section One</a></li>
        <li><a href="#section2">Section Two</a></li>
    </ol>
</div>
```

Then use `id="section1"` on your h2 elements.

### 4. Callout Boxes
Four styles available: info, tip, warning, quote

```html
<div class="callout callout-info not-prose">
    <strong>Note:</strong> Important information here.
</div>

<div class="callout callout-tip not-prose">
    <strong>Pro tip:</strong> Helpful advice here.
</div>

<div class="callout callout-warning not-prose">
    <strong>Warning:</strong> Caution message here.
</div>
```

### 5. Feature Cards Grid
Great for skills, benefits, or features:

```html
<div class="feature-grid not-prose">
    <div class="feature-card">
        <div class="feature-card-icon">🎯</div>
        <h4>Feature Title</h4>
        <p>Feature description here.</p>
    </div>
    <!-- More cards... -->
</div>
```

### 6. Organization Cards
For listing companies/orgs with badges:

```html
<div class="org-card not-prose">
    <div class="org-card-badge">🌍</div>
    <div class="org-card-content">
        <h3>Organization Name</h3>
        <p>Description of the organization and what they do.</p>
        <div class="org-card-tags">
            <span class="org-tag">🔬 Research</span>
            <span class="org-tag">💰 Well-funded</span>
        </div>
    </div>
</div>
```

### 7. Icon Lists
Replace boring bullets with icons:

```html
<ul class="icon-list check not-prose">
    <li>Item with checkmark</li>
</ul>

<ul class="icon-list arrow not-prose">
    <li>Item with arrow</li>
</ul>

<ul class="icon-list star not-prose">
    <li>Item with star</li>
</ul>
```

### 8. Section Dividers
Visual break between sections:

```html
<div class="section-divider not-prose">Section Name</div>
```

### 9. FAQ Accordions
Collapsible Q&A sections:

```html
<details class="group mb-4 border border-gray-200 rounded-xl overflow-hidden">
    <summary class="flex items-center justify-between p-5 bg-gray-50 cursor-pointer hover:bg-gray-100 transition">
        <span class="font-semibold text-gray-900">Question here?</span>
        <svg class="w-5 h-5 text-gray-500 transform group-open:rotate-180 transition" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
    </summary>
    <div class="p-5 border-t border-gray-200">
        <p class="text-gray-600">Answer here.</p>
    </div>
</details>
```

### 10. Reading Progress Bar
Add to top of page + JavaScript:

```html
<div class="reading-progress" id="reading-progress"></div>

<script>
window.addEventListener('scroll', function() {
    const article = document.querySelector('article');
    const progressBar = document.getElementById('reading-progress');
    if (!article || !progressBar) return;
    
    const articleTop = article.offsetTop;
    const articleHeight = article.offsetHeight;
    const windowHeight = window.innerHeight;
    const scrollY = window.scrollY;
    
    const progress = Math.min(100, Math.max(0, 
        ((scrollY - articleTop + windowHeight * 0.3) / (articleHeight - windowHeight * 0.5)) * 100
    ));
    
    progressBar.style.width = progress + '%';
});
</script>
```

### 11. Read Time Badge
Display estimated reading time:

```html
<span class="read-time">
    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
    10 min read
</span>
```

## Important: `not-prose` Class

When using these components inside a `prose` container, always add `not-prose` class to prevent Tailwind's typography plugin from overriding the styles.

## Pages Updated

✅ Climate & Environment pillar (`pillars/climate-environment.html`)
✅ AI Safety pillar (`pillars/ai-safety.html`)
✅ Climate Top Employers cluster (`clusters/climate-top-remote-employers.html`)
✅ Blog detail (`blog/detail.html`)

## Pages Still Needing Updates

### Pillars
- `pillars/global-health.html`
- `pillars/humanitarian-ingo.html`
- `pillars/effective-altruism.html`

### Clusters (15 total)
- `clusters/ai-safety-alignment-researcher-path.html`
- `clusters/ai-safety-governance-fellowships.html`
- `clusters/ai-safety-non-technical-roles.html`
- `clusters/climate-break-into-no-stem.html`
- `clusters/climate-career-switch-90-day.html`
- `clusters/ea-grantmaking-career.html`
- `clusters/ea-mid-career-switch.html`
- `clusters/ea-operations-roles.html`
- `clusters/health-remote-without-mph.html`
- `clusters/health-tech-startups-2026.html`
- `clusters/health-un-agency-guide.html`
- `clusters/ingo-mel-skills-career.html`
- `clusters/ingo-no-field-experience.html`
- `clusters/ingo-un-consultancy-guide.html`

## Reference Examples

See these files for the full pattern:
- `templates/resources/pillars/climate-environment.html` (pillar pattern)
- `templates/resources/clusters/climate-top-remote-employers.html` (cluster pattern)
