"""Close a captured playback handle even if ASGI sending fails before iteration."""

def owned_stream(response_class, stream, iterator, **options):
    class OwnedMediaResponse(response_class):
        def close(self): stream.close()
        async def __call__(self, *args, **kwargs):
            try: return await super().__call__(*args, **kwargs)
            finally: self.close()
    try: return OwnedMediaResponse(iterator, **options)
    except BaseException:
        stream.close()
        raise
