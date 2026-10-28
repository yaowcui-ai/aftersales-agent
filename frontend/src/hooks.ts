import { useCallback, useEffect, useRef, useState } from "react";
import { ApiClient } from "./api";

export function useResource<T>(api: ApiClient, path: string) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(true);
  const generation = useRef(0);
  const refresh = useCallback(async () => {
    const current = ++generation.current;
    setLoading(true);
    setError(undefined);
    try {
      const result = await api.get<T>(path);
      if (current === generation.current) setData(result);
    } catch (caught) {
      if (current === generation.current) setError(caught);
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }, [api, path]);
  useEffect(() => {
    setData(undefined);
    void refresh();
    return () => {
      generation.current++;
    };
  }, [refresh]);
  return { data, error, loading, refresh, setData };
}
