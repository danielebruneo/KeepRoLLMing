#!/usr/bin/env python3
"""
Generate SKILLS-INDEX.md from all skill directories.
Scans .catalyst/_skills/, _project/_skills/, and _agent/overlay/_skills/
to create a comprehensive index grouped by topic.
"""

import os
import re
from pathlib import Path
from datetime import datetime


def extract_skill_metadata(skill_dir: Path) -> dict | None:
    """Extract metadata from a skill directory."""
    skill_md = skill_dir / "SKILL.md"
    
    if not skill_md.exists():
        return None
    
    content = skill_md.read_text(encoding="utf-8")
    
    # Try to extract frontmatter description
    desc_match = re.search(r'---\s*\n.*?description:\s*(.+?)\s*\n.*?---', content, re.DOTALL)
    description = desc_match.group(1).strip() if desc_match else None
    
    # Extract goal/purpose from first section
    goal_match = re.search(r'#.*?\n+(?:## Goal\n+)([^\n]+)', content, re.MULTILINE | re.DOTALL)
    goal = goal_match.group(1).strip() if goal_match else None
    
    return {
        "name": skill_dir.name,
        "description": description or (goal.split(" - ")[-1] if goal and " - " in goal else goal),
        "goal": goal,
        "location": f".catalyst/_skills/{skill_dir.name}/SKILL.md"
    }


def categorize_skills(skills: list[dict]) -> dict[str, list[dict]]:
    """Group skills into logical categories."""
    categories = {
        "Task Management & Workflow": [],
        "Documentation & Knowledge": [],
        "Repository Maintenance": [],
        "Code Quality & Testing": [],
        "Catalyst Core Operations": [],
        "Feature Development": []
    }
    
    task_keywords = ["task", "todo", "active", "complete", "pickup", "close"]
    doc_keywords = ["doc", "documentation", "readme", "knowledge", "consolidate", "review"]
    maint_keywords = ["sync", "build", "map", "registry", "bootstrap", "command"]
    code_keywords = ["refactor", "test", "fix", "implement", "quality"]
    catalyst_keywords = ["catalyst", "legacy", "reconcile", "propose", "digest"]
    feature_keywords = ["feature", "add", "implement"]
    
    for skill in skills:
        name_lower = skill["name"].lower()
        desc_lower = (skill.get("description") or "").lower()
        
        combined = f"{name_lower} {desc_lower}"
        
        if any(kw in combined for kw in task_keywords):
            categories["Task Management & Workflow"].append(skill)
        elif any(kw in combined for kw in doc_keywords):
            categories["Documentation & Knowledge"].append(skill)
        elif any(kw in combined for kw in maint_keywords):
            categories["Repository Maintenance"].append(skill)
        elif any(kw in combined for kw in code_keywords):
            categories["Code Quality & Testing"].append(skill)
        elif any(kw in combined for kw in catalyst_keywords):
            categories["Catalyst Core Operations"].append(skill)
        elif any(kw in combined for kw in feature_keywords):
            categories["Feature Development"].append(skill)
        else:
            # Default to Documentation & Knowledge if unsure
            categories["Documentation & Knowledge"].append(skill)
    
    return categories


def scan_skill_directories() -> list[dict]:
    """Scan all skill directories and collect metadata."""
    # Base path is the repository root (parent of .catalyst)
    base_path = Path(__file__).resolve().parents[3]  # Go up from generate_index.py to repo root
    skills = []
    
    print(f"Base path: {base_path}")
    
    # Scan core skills
    core_skills_dir = base_path / ".catalyst" / "_skills"
    if core_skills_dir.exists():
        for item in core_skills_dir.iterdir():
            if item.is_dir():  # Only process directories, not files
                metadata = extract_skill_metadata(item)
                if metadata:
                    skills.append(metadata)
    
    # Scan project-specific skills
    project_skills_dir = base_path / "_project" / "_skills"
    if project_skills_dir.exists():
        for item in project_skills_dir.iterdir():
            if item.is_dir() and item.name != "README.md":  # Only process directories
                metadata = extract_skill_metadata(item)
                if metadata:
                    # Update location for project skills
                    metadata["location"] = f"_project/_skills/{item.name}/SKILL.md"
                    skills.append(metadata)
    
    return skills


def generate_markdown(categories: dict[str, list[dict]]) -> str:
    """Generate the SKILLS-INDEX.md content."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    lines = [
        "# Skills Index",
        "",
        f"**Generated:** {timestamp}",
        "",
        "## Overview",
        "",
        "This index provides a comprehensive map of all available CATALYST skills, organized by functional category. Use this reference to quickly discover and leverage the appropriate skill for your task.",
        "",
        "## Quick Reference",
        ""
    ]
    
    # Add summary table
    lines.append("| Category | Skills Count |")
    lines.append("|----------|--------------|")
    for category, skills in categories.items():
        lines.append(f"| {category} | {len(skills)} |")
    
    lines.extend(["", "## Detailed Categories", ""])
    
    # Add each category
    for category, skills in categories.items():
        if not skills:
            continue
            
        lines.append(f"### {category}")
        lines.append("")
        
        for skill in sorted(skills, key=lambda x: x["name"]):
            name = skill["name"]
            desc = skill.get("description") or "No description available"
            location = skill.get("location", "Unknown")
            
            # Create link using directory name as anchor
            anchor = name.lower().replace("-", "-").replace("_", "-")
            
            lines.append(f"- **[{name}]**({location}) - {desc}")
        
        lines.append("")
    
    # Add usage guidelines
    lines.extend([
        "## Usage Guidelines",
        "",
        "### How to Use Skills",
        "",
        "1. **Identify Your Task**: Determine what type of work you need to accomplish",
        "2. **Find the Category**: Locate the relevant category in this index",
        "3. **Select a Skill**: Choose the skill that best matches your needs",
        "4. **Invoke the Skill**: Use `skill: \"SKILL-NAME\"` to execute it",
        "",
        "### Best Practices",
        "",
        "- Always read the SKILL.md file for detailed instructions before using a skill",
        "- Skills are markdown procedures, not executables - they guide agent behavior",
        "- Use `INDEX-SKILLS` skill when adding new skills to update this index",
        "- Check `_agent/state/ACTIVE_TASK.md` for context on current work",
        "",
        "## Skill Categories Explained",
        "",
        "**Task Management & Workflow**: Skills for managing tasks, TODOs, and workflow progression",
        "**Documentation & Knowledge**: Skills for creating, improving, and maintaining documentation",
        "**Repository Maintenance**: Skills for keeping the repository structure and metadata up to date",
        "**Code Quality & Testing**: Skills for refactoring code and fixing tests",
        "**Catalyst Core Operations**: Skills for managing CATALYST framework itself",
        "**Feature Development**: Skills for implementing new features",
        "",
        "## Maintenance",
        "",
        "This index is auto-generated by the **INDEX-SKILLS** skill. When adding or modifying skills:",
        "1. Create/update the SKILL.md file in the appropriate directory",
        "2. Run the INDEX-SKILLS skill to regenerate this index",
        "3. Verify that all skills are correctly categorized",
        "",
        "---",
        f"*Generated by INDEX-SKILLS on {timestamp}*"
    ])
    
    return "\n".join(lines)


def main():
    """Main entry point."""
    # Base path is the repository root (parent of .catalyst)
    base_path = Path(__file__).resolve().parents[3]  # Go up from generate_index.py to repo root
    
    print(f"Base path: {base_path}")
    print("Scanning skill directories...")
    
    # Check if core skills directory exists
    core_skills_dir = base_path / ".catalyst" / "_skills"
    print(f"Core skills dir exists: {core_skills_dir.exists()}")
    if core_skills_dir.exists():
        print(f"Contents: {list(core_skills_dir.iterdir())[:5]}...")
    
    skills = scan_skill_directories()
    print(f"Found {len(skills)} skills")
    
    print("Categorizing skills...")
    categories = categorize_skills(skills)
    
    print("Generating SKILLS-INDEX.md...")
    markdown_content = generate_markdown(categories)
    
    # Ensure _project/_skills directory exists
    output_dir = base_path / "_project" / "_skills"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Write to _project/_skills/SKILLS-INDEX.md
    output_path = output_dir / "SKILLS-INDEX.md"
    output_path.write_text(markdown_content, encoding="utf-8")
    
    print(f"✓ Generated {output_path}")
    print("\nCategories created:")
    for category, skills_list in categories.items():
        if skills_list:
            print(f"  - {category}: {len(skills_list)} skills")


if __name__ == "__main__":
    main()
