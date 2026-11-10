"""Check the spelling of the German demo labels with hunspell and its German
dictionary, which also knows compound words like "Brandschutztür". The
spelling job of the CI runs it. Locally it needs hunspell and the German
dictionary, e.g. "apt install hunspell hunspell-de-de"."""
import glob
import json
import os
import re
import subprocess
import sys

DEMOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app', 'labeldesigner', 'demos')
# Correct, but not in the dictionary: technical terms, compound words and
# abbreviations, also in capitals
KNOWN = {'AED', 'Brandfördernd', 'Chili', 'Dampflok', 'Defibrillator', 'Ibuprofen', 'Inventaretikett', 'Labelburg', 'mg', 'Minze', 'ml', 'Nr',
         'Olivenöl', 'Oxidationsmittel', 'Patchfeld', 'Pesto', 'Planierraupe', 'QR', 'Rack', 'Reagenz', 'Regaletikett', 'Rutschgefahr',
         'Umweltgefährlich', 'Verbandkasten'}


def label_words():
    """The words of each German demo label, by file."""
    for path in sorted(glob.glob(os.path.join(DEMOS, '*', '*-de.json'))):
        with open(path, encoding='utf-8') as fh:
            label = json.load(fh)
        text = '\n'.join([label['title']] + [line['text'] for line in label['text']])
        # A word broken over two lines, e.g. "Notruf-" and "telefon"
        text = re.sub(r'-\n(?=[a-zäöüß])', '', text)
        # Templates like {{datetime:%d.%m.%Y}}
        text = re.sub(r'\{\{[^}]*\}\}', ' ', text)
        # Addresses like github.com/DL6ER/labelburg
        text = re.sub(r'\S+\.(?:com|de|net|org)/\S*', ' ', text)
        yield os.path.relpath(path, DEMOS), set(re.findall(r'[A-Za-zÄÖÜäöüß]+', text))


def main():
    labels = list(label_words())
    words = sorted(set().union(*(words for _, words in labels)))
    result = subprocess.run(['hunspell', '-i', 'utf-8', '-d', 'de_DE', '-l'], input='\n'.join(words),
                            capture_output=True, text=True, encoding='utf-8', check=True)
    known = {word.lower() for word in KNOWN}
    unknown = {word for word in result.stdout.split() if word.lower() not in known}
    for name, label in labels:
        for word in sorted(label & unknown):
            print(f'{name}: {word}')
    print(f'{len(labels)} German demo labels, {len(words)} words, {len(unknown)} unknown')
    return 1 if unknown else 0


if __name__ == '__main__':
    sys.exit(main())
