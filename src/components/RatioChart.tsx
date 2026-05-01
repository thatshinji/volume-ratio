import { useEffect, useRef } from "react";
import { createChart, ColorType, LineSeries, type IChartApi, type ISeriesApi, type LineData } from "lightweight-charts";

interface RatioChartProps {
  data: { time: string; value: number }[];
  height?: number;
}

export default function RatioChart({ data, height = 200 }: RatioChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      height,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#6b7280",
      },
      grid: {
        vertLines: { color: "#f3f4f6" },
        horzLines: { color: "#f3f4f6" },
      },
      rightPriceScale: {
        borderColor: "#e5e7eb",
      },
      timeScale: {
        borderColor: "#e5e7eb",
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        vertLine: { labelBackgroundColor: "#3b82f6" },
        horzLine: { labelBackgroundColor: "#3b82f6" },
      },
    });

    const series = chart.addSeries(LineSeries, {
      color: "#3b82f6",
      lineWidth: 2,
    });

    // Add threshold lines
    const thresholdHigh = chart.addSeries(LineSeries, {
      color: "#ef4444",
      lineWidth: 1,
      lineStyle: 2,
      lastValueVisible: false,
      priceLineVisible: false,
    });

    const thresholdLow = chart.addSeries(LineSeries, {
      color: "#f59e0b",
      lineWidth: 1,
      lineStyle: 2,
      lastValueVisible: false,
      priceLineVisible: false,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    // Set threshold data
    if (data.length > 0) {
      const times = data.map((d) => d.time);
      thresholdHigh.setData(
        times.map((t) => ({ time: t as string, value: 2.0 }))
      );
      thresholdLow.setData(
        times.map((t) => ({ time: t as string, value: 0.6 }))
      );
    }

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    if (seriesRef.current && data.length > 0) {
      seriesRef.current.setData(data as LineData[]);
      chartRef.current?.timeScale().fitContent();
    }
  }, [data]);

  return <div ref={containerRef} className="w-full" />;
}
