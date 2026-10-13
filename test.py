def main():
    from googlesearch import search

    query = 'site:boards.greenhouse.io "non-profit" remote'
    for result in search(query, num_results=10, advanced=True):
        print(f"Title: {result.title}")
        print(f"URL: {result.url}")
        print(f"Description: {result.description}\n")


if __name__ == "__main__":
    main()
