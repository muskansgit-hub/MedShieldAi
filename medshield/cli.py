"""Score one answer from the terminal:
    python -m medshield.cli "Can I take ibuprofen with warfarin?" "Yes, it's perfectly safe."
"""
import json
import sys

from .guard import Guard


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    rep = Guard().analyze(sys.argv[1], sys.argv[2]).to_dict()
    print(f"tier {rep['risk']['tier']}  C={rep['confidence']['score']:.2f}  "
          f"I={rep['incorrectness']['score']:.2f}  FSC={rep['fsc']:.3f}  ->  {rep['action']}")
    for r in rep["reasons"]:
        print("  -", r)
    if rep["final_response"] != rep["original_response"]:
        print("\nDelivered:\n" + rep["final_response"])
    if "--json" in sys.argv:
        print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
