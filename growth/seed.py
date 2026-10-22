"""Bounded editorial experiments based on our existing research, not traffic forecasts."""
from django.db import transaction
from django.utils import timezone
from blog.models import BlogPost
from .models import ArticleRevision, EditorialOpportunity


OPPORTUNITIES = [
    ("remote-hiring-description-checklist", "A clearer remote impact job description", "employer", "/employers/", "Original employer checklist; compare with the existing job-description tool before deciding to publish."),
    ("hiring-campaign-measurement", "What a hiring campaign report can—and cannot—tell you", "employer", "/employers/", "Explain views versus outbound clicks versus confirmed applications; support an honest pilot offer."),
    ("country-aware-scout-guide", "How to search for remote impact work from your country", "seeker", "/alerts/", "Draft only; overlaps the planned country guide. Prefer updating that destination rather than adding two pages."),
    ("employer-role-eligibility", "Who can actually apply for your remote role?", "employer", "/employers/", "Potential section of the description checklist, not automatically a separate article."),
    ("employer-campaign-brief", "A practical brief for a small impact hiring campaign", "employer", "/employers/", "Needs actual paid-pilot evidence before discussing results or recurring packages."),
    ("climate-career-refresh", "Review the climate career guide against current listings", "seeker", "/resources/climate-environment-careers/", "Refresh an existing guide; no additional URL proposed."),
    ("nonprofit-inventory-depth", "Explain the limits of our remote nonprofit inventory", "seeker", "/impact/remote-nonprofit-jobs/", "Existing destination. Needs dated source/coverage evidence, not search-volume promises."),
    ("software-role-refresh", "Improve remote software engineering discovery", "seeker", "/remote-software-engineer-jobs/", "Existing destination; preserve geography and must-have skill constraints."),
    ("impact-market-snapshot", "Refresh the dated impact hiring snapshot", "community", "/insights/remote-impact-jobs/", "Existing destination. Count deduplicated available jobs and retain source concentration caveats."),
    ("scout-value-explainer", "When a recurring Scout is useful", "seeker", "/pricing/", "Needs actual usefulness/retention evidence. Do not imply free Scouts recur or trials are paid demand."),
]


DRAFTS = {
    "remote-hiring-description-checklist": (
        "Help candidates understand the work, eligibility and application process before they apply.",
        """<h2>Start with the work, not a list of adjectives</h2>
<p>Describe the problem this person will own and two or three outcomes you expect. Separate responsibilities from qualifications. A candidate should be able to explain what they would do in a typical week.</p>
<h2>Make remote eligibility explicit</h2>
<p>State the countries or regions from which you can hire. Explain required working-hour overlap, travel and whether the arrangement is employment or contracting. “Remote” alone does not answer those questions. Have the appropriate person in your organization check legal and employment details; this checklist is not legal advice.</p>
<h2>Explain compensation in usable units</h2>
<p>If you publish a pay range, include the currency, period and working hours. Explain whether location changes the range and distinguish base pay from other compensation. Do not ask applicants to compare an hourly figure with an annual salary without the relevant assumptions.</p>
<h2>Keep requirements honest</h2>
<p>Separate essential capabilities from skills someone can learn. Describe why a credential is necessary rather than copying it from an older role. Check that title, responsibilities and seniority describe the same job.</p>
<h2>Show the route to applying</h2>
<p>Give applicants the current destination, requested materials, deadline and anticipated next steps. If the role closes, update its source page and any places where you have shared it.</p>
<h2>Review before distribution</h2>
<ul><li>Can the intended candidate work from an eligible location?</li><li>Are responsibilities and must-have skills distinguishable?</li><li>Are any pay figures comparable and clearly labelled?</li><li>Does the application link lead to this open role?</li></ul>
<p>This is an editorial draft, not a claim that clearer wording guarantees applications or hires. An employer should approve the final role description before publication.</p>"""),
    "hiring-campaign-measurement": (
        "A useful hiring report separates observed engagement from applications and hires.",
        """<h2>Begin with what was actually delivered</h2>
<p>A report should list the agreed role page, any approved story or creative, the channels actually used and their delivery dates. A promised channel is not a delivered channel. Keep screenshots, publication URLs or sending receipts where appropriate.</p>
<h2>Do not turn clicks into applications</h2>
<p>A listing view records an interaction with a role page. An application-link click records a move toward an application destination. Neither proves that the person submitted an application, met the requirements or was hired.</p>
<p>Counts can include repeated activity across dates and roles. Explain the observation window, deduplication method, known exclusions and missing information. Avoid describing every recorded event as a different person.</p>
<h2>Ask for employer-side evidence</h2>
<p>Where the employer can measure it, request aggregate attributed applications, qualified applicants and interviews. Keep “not measured” separate from zero. Do not collect private applicant details simply to make a marketing report look more complete.</p>
<h2>Report the economics too</h2>
<p>Compare the campaign fee with actual delivery effort and relevant costs. A sale can be real while the service is still uneconomic to deliver. Record editing, review, distribution and support time instead of treating founder time as free.</p>
<h2>Make the renewal decision useful</h2>
<p>Ask whether another campaign would solve a current hiring need, what should change and which outcome would justify its cost. A one-off purchase is not recurring revenue. Renewal evidence matters more than a large impression number.</p>
<p>No audience size, application rate or hiring outcome is asserted in this draft. Those require campaign-specific evidence and buyer review.</p>"""),
    "country-aware-scout-guide": (
        "Treat remote location, role fit and salary evidence as separate checks.",
        """<h2>Start with where you will work</h2>
<p>A remote role may still have hiring-country, working-hours or work-authorization restrictions. Start with the country where you expect to work and read the employer's own location requirements. A remote label does not establish your eligibility.</p>
<h2>Describe the work you want</h2>
<p>State a role or responsibilities, useful skills and the impact areas you care about. Distinguish the work you want next from your past experience. Include genuine exclusions, but avoid filling a search with requirements that are only preferences.</p>
<h2>Make pay requirements comparable</h2>
<p>If pay is a hard requirement, specify the minimum, currency and time period. A listing that does not publish pay cannot establish that it meets your minimum. Currency conversions, tax and living-cost assumptions need separate consideration.</p>
<h2>Read the result's caveats</h2>
<p>A possible match with uncertain location or responsibilities is something to check, not a verified fit. Compare the result with the current source listing. If it is closed or its requirements differ, use the employer's current information.</p>
<h2>Separate an empty search from an interrupted check</h2>
<p>No confirmed match can mean limited current inventory or an incomplete review. Those are different outcomes. Do not remove an essential country or pay restriction simply to produce a longer list. Recheck the same requirements when a temporary issue is resolved.</p>
<p>On RemoteImpact, the free Scout is a one-time matching email; recurring searches belong to the paid offering. This guide does not promise that every country or role has current coverage, or that a match guarantees an interview.</p>"""),
}


@transaction.atomic
def seed_editorial():
    result = {"opportunities_created": 0, "drafts_created": 0}
    for key, title, audience, destination, note in OPPORTUNITIES:
        evidence_url = "https://remoteimpact.org" + destination
        opportunity, created = EditorialOpportunity.objects.get_or_create(key=key, defaults={
            "title": title, "audience": audience, "intent": note, "destination_path": destination,
            "evidence": [{"url": evidence_url, "captured_at": timezone.now().isoformat(),
                          "note": "Candidate destination from repository review and 2026-10-19 research. Live facts and overlap still require editor verification."}],
            "coverage_notes": "Hypothesis, not a validated keyword opportunity. No traffic forecast, audience or salary statistics supplied.",
        })
        result["opportunities_created"] += created
        if key not in DRAFTS or opportunity.revisions.exists():
            continue
        # Never claim/update an existing slug owned by another article or editor.
        slug = "editorial-draft-" + key
        if BlogPost.objects.filter(slug=slug).exists():
            continue
        excerpt, body = DRAFTS[key]
        post = BlogPost.objects.create(title=title, slug=slug, excerpt=excerpt, body="", status="draft")
        ArticleRevision.objects.create(opportunity=opportunity, post=post, title=title, excerpt=excerpt, body=body)
        result["drafts_created"] += 1
    return result
