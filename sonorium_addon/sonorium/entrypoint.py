def main():
    import sys
    try:
        from sonorium.settings import settings
        return settings.run()
    except Exception as e:
        print(f"FATAL: Failed to start Sonorium: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
