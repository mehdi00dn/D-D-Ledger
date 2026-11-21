"""DL-180 part 19: sentence case for every label, button, tab and secondary heading; Title Case only for the page's primary heading (h1)."""
import glob
import re

TITLE_CASE_BUTTON = re.compile(r">\s*(?:\{\{[^}]*\}\}\s*)?([A-Z][a-z]+ (?:[A-Z][a-z]+))\s*<")
# Words a label may carry in capitals: a name, a place or a product.
KEEP = ('Oaken', 'Dungeon Master', 'Your characters', 'Creative Commons', 'Open Font', 'Lottie', 'System Reference', 'Newsreader', 'Lato')


def _visible_labels(path):
    text = open(path, encoding='utf-8').read()
    text = re.sub(r'<!--.*?-->|<script\b.*?</script>|<style\b.*?</style>', '', text, flags=re.S)
    text = re.sub(r'<h1\b.*?</h1>', '', text, flags=re.S)                       # primary headings keep Title Case
    for tag in re.finditer(r'<(button|label|h2|h3|h4|summary|option|th|legend)\b[^>]*>(.*?)</\1>', text, flags=re.S):
        yield tag.group(1), re.sub(r'\{\{.*?\}\}|\{%.*?%\}|<[^>]+>|&amp;', ' ', tag.group(2)).strip()
    for m in re.finditer(r"<a\b[^>]*class=\"[^\"]*\bbtn\b[^\"]*\"[^>]*>(.*?)</a>", text, flags=re.S):
        yield 'a.btn', re.sub(r'\{\{.*?\}\}|\{%.*?%\}|<[^>]+>', ' ', m.group(1)).strip()


def test_no_label_button_or_secondary_heading_is_in_title_case():
    offenders = []
    for path in sorted(glob.glob('templates/**/*.html', recursive=True)):
        for kind, label in _visible_labels(path):
            label = re.sub(r'\s+', ' ', label)
            words = re.findall(r"[A-Za-z][A-Za-z'-]*", label)
            if len(words) < 2 or len(label) > 50 or kind == 'option' or any(k in label for k in KEEP):
                continue                                                        # long runs are prose with several sentences; options are data
            capitals = [w for w in words[1:] if w[0].isupper() and not w.isupper() and w not in ('Space', 'Esc', 'Ctrl', 'Shift', 'Enter', 'NPCs', 'Attach', 'Parchment', 'Easy', 'Average', 'Rolled', 'Build', 'Keep', 'Lvl')]
            if capitals:
                offenders.append((path, kind, label))
    assert not offenders, offenders


def test_primary_headings_keep_title_case():
    # the page title of the forms and the policy pages is the one place Title Case stays
    for path, heading in (('templates/campaign_form.html', "'New Campaign'"), ('templates/character_form.html', "'New Character'"), ('templates/group_form.html', "'New Faction'"), ('templates/reference.html', 'Rules Reference'), ('templates/about.html', 'About Oaken')):
        assert heading in open(path, encoding='utf-8').read(), (path, heading)


def test_js_built_labels_are_sentence_case():
    js = open('static/js/cropper.js', encoding='utf-8').read() + open('static/js/battle.js', encoding='utf-8').read()
    for old in ('Position Avatar', 'Position Banner Image', 'Position Campaign Image', 'No Faction'):
        assert old not in js, old
