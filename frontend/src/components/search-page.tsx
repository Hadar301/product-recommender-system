import { Skeleton, Title, Divider } from '@patternfly/react-core';
import { LazyProductGallery } from './LazyProductGallery';
import { usePersonalizedRecommendations } from '../hooks/useRecommendations';
import { ImageSearch } from './ImageSearch';

export function SearchPage() {
  const { data, isError, isLoading } = usePersonalizedRecommendations();

  return (
    <div>
      <Title headingLevel={'h1'} style={{ paddingBottom: 20 }}>
        Search
      </Title>

      {/* Add Image Search Component */}
      <div style={{ marginBottom: 20 }}>
        <ImageSearch />
      </div>

      <Divider style={{ marginBottom: 20 }} />

      {isLoading ? (
        <Skeleton style={{ height: 200 }} />
      ) : isError ? (
        <div>Error fetching products</div>
      ) : (
        <LazyProductGallery
          products={data ?? []}
          title='Recommended Products'
          showProductCount={true}
          showScrollToTop={true}
        />
      )}
    </div>
  );
}
