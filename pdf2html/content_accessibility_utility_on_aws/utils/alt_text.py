"""Consistent, screen-reader-visible disclosure for generated image descriptions."""
import re

AI_ALT_PREFIX = 'AI generated alt text:'


def set_ai_alt(img, description):
    text = description.strip()
    # Reprocessing or a model-supplied label must not stack repeated disclosures.
    text = re.sub(r'^(?:AI generated alt text:\s*)+', '', text, flags=re.IGNORECASE)
    img['alt'] = f'{AI_ALT_PREFIX} {text}' if text else ''
    img['data-alt-status'] = 'ai-generated'
    return img['alt']
