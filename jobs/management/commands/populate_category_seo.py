"""
Management command to populate SEO-optimized category descriptions.
These descriptions help Google understand what each category page is about
and improve rankings for "remote [category] jobs" queries.
"""
from django.core.management.base import BaseCommand
from jobs.models import Category


# SEO-optimized descriptions for each category
CATEGORY_DESCRIPTIONS = {
    "advocacy-or-policy": "Find remote advocacy and policy jobs making a difference in public affairs, lobbying, and legislative work. Connect with organizations driving systemic change from anywhere in the world.",
    
    "ai-safety": "Discover remote AI safety and governance roles focused on ensuring artificial intelligence benefits humanity. Work on alignment research, AI policy, and responsible AI development.",
    
    "animal-welfare": "Browse remote animal welfare jobs dedicated to protecting and advocating for animals. Join organizations working on farm animal advocacy, wildlife conservation, and companion animal care.",
    
    "biosecurity": "Explore remote biosecurity and pandemic preparedness positions protecting global health. Work on disease surveillance, biodefense policy, and public health emergency response.",
    
    "buildings": "Find remote jobs in sustainable buildings and green construction. Work on energy-efficient design, LEED certification, and building decarbonization projects.",
    
    "capital": "Discover remote impact investing and sustainable finance roles. Join funds and organizations deploying capital for social and environmental returns.",
    
    "climate-environment": "Browse remote climate and environment jobs tackling the climate crisis. Work on carbon reduction, environmental policy, conservation, and sustainability initiatives.",
    
    "coastal-ocean-sinks": "Find remote ocean and coastal conservation roles protecting marine ecosystems. Work on blue carbon projects, marine protected areas, and ocean health initiatives.",
    
    "communications": "Explore remote communications and media jobs amplifying impact stories. Join teams creating compelling content for social change and mission-driven organizations.",
    
    "education": "Discover remote education and research positions advancing knowledge and learning. Work on educational equity, curriculum development, and academic research for impact.",
    
    "effective-altruism": "Find remote effective altruism jobs focused on doing the most good. Join organizations using evidence and reason to figure out how to benefit others as much as possible.",
    
    "energy": "Browse remote clean energy jobs accelerating the renewable transition. Work on solar, wind, energy storage, grid modernization, and energy access projects.",
    
    "food-agriculture-land-use": "Explore remote food, agriculture, and land use roles building sustainable food systems. Work on regenerative agriculture, food security, and land conservation.",
    
    "gender-equality-social-inclusion": "Find remote GESI jobs advancing gender equality and social inclusion. Join organizations promoting women's empowerment, LGBTQ+ rights, and inclusive development.",
    
    "global-health": "Discover remote global health positions improving health outcomes worldwide. Work on disease prevention, health systems strengthening, and healthcare access.",
    
    "humanitarian": "Browse remote humanitarian and disaster relief jobs responding to crises. Join organizations providing emergency aid, refugee support, and disaster recovery.",
    
    "human-rights": "Explore remote human rights and justice roles defending fundamental freedoms. Work on civil liberties, criminal justice reform, and international human rights.",
    
    "impact-careers": "Find remote impact career support roles helping others find meaningful work. Join career services, job boards, and professional development organizations.",
    
    "materials-manufacturing": "Discover remote sustainable materials and manufacturing jobs. Work on circular economy solutions, sustainable supply chains, and clean manufacturing.",
    
    "media-journalism": "Browse remote media and journalism jobs telling important stories. Join newsrooms and media organizations covering social issues, climate, and democracy.",
    
    "nonprofit-charity": "Find remote nonprofit and charity jobs across various causes. Work for mission-driven organizations making a difference in communities worldwide.",
    
    "nuclear-security": "Explore remote nuclear security positions preventing nuclear threats. Work on nonproliferation, nuclear policy, and global security initiatives.",
    
    "operations": "Discover remote operations and administration roles keeping impact organizations running. Join teams managing finance, HR, IT, and organizational effectiveness.",
    
    "other": "Browse other remote impact jobs across emerging and specialized fields. Find unique opportunities to make a difference in new and evolving areas.",
    
    "other-1": "Explore additional remote impact opportunities in specialized niches. Discover roles that don't fit traditional categories but create meaningful change.",
    
    "policy-advocacy": "Find remote policy and advocacy jobs shaping public decisions. Work on campaign strategy, government relations, and grassroots organizing.",
    
    "poverty-development": "Discover remote poverty alleviation and economic development roles. Join organizations working on financial inclusion, livelihoods, and global development.",
    
    "technology": "Browse remote technology and engineering jobs building tools for good. Work on tech for social impact, civic tech, and software for mission-driven organizations.",
    
    "transportation": "Find remote sustainable transportation jobs transforming mobility. Work on electric vehicles, public transit, bike infrastructure, and transportation equity.",
}


class Command(BaseCommand):
    help = 'Populate SEO-optimized descriptions for job categories'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            help='Overwrite existing descriptions',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        overwrite = options['overwrite']
        
        updated = 0
        skipped = 0
        not_found = []
        
        for slug, description in CATEGORY_DESCRIPTIONS.items():
            try:
                category = Category.objects.get(slug=slug)
                
                if category.description and not overwrite:
                    self.stdout.write(f"  Skipped (has description): {category.name}")
                    skipped += 1
                    continue
                
                if dry_run:
                    self.stdout.write(f"  Would update: {category.name}")
                    self.stdout.write(f"    {description[:80]}...")
                else:
                    category.description = description
                    category.save(update_fields=['description'])
                    self.stdout.write(self.style.SUCCESS(f"  Updated: {category.name}"))
                
                updated += 1
                
            except Category.DoesNotExist:
                not_found.append(slug)
        
        # Summary
        self.stdout.write("\n" + "="*50)
        if dry_run:
            self.stdout.write(f"DRY RUN - Would update {updated} categories")
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated {updated} categories"))
        
        if skipped:
            self.stdout.write(f"Skipped {skipped} (already have descriptions)")
        
        if not_found:
            self.stdout.write(self.style.WARNING(f"\nCategories not found: {', '.join(not_found)}"))
            
        # Show any categories without descriptions
        missing = Category.objects.filter(description='') | Category.objects.filter(description__isnull=True)
        if missing.exists():
            self.stdout.write(self.style.WARNING(f"\nCategories still missing descriptions:"))
            for cat in missing:
                self.stdout.write(f"  - {cat.name} (slug: {cat.slug})")
