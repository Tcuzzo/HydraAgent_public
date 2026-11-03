---
name: design-critique
description: Critique a product surface against its actual user task, evidence hierarchy, affordances, motion, accessibility, and visual coherence; deliver specific fixes and verify them on the live surface.
license: MIT
---

# Design Critique

Use `design-taste` for the project's visual and product standards. Review the
actual surface at the user's viewport, not a component screenshot alone.

1. Name the user decision the surface should improve and what evidence makes
   that decision better. If the surface is a graph, trace a visible relationship
   to its sources, confidence, competing explanations, and an actionable
   workflow. A decorative graph without this path is not complete.
2. Walk the primary task, its failure state, settings, navigation, and return
   path. Test keyboard access, focus order, contrast, motion preferences, and
   small screens.
3. Rank findings by user consequence and cite the exact screen or interaction.
   Separate observed defects from taste judgments and unknowns.
4. Implement the smallest coherent correction in the intended repository.
   Revisit the live surface after the change; capture evidence for each fixed
   high-severity finding.

Do not label static animation as capability. Motion should show state or
causality and stop or simplify when reduced motion is requested.
